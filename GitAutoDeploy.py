#!/usr/bin/env python


class Lock():
    """Simple implementation of a mutex lock using the file systems. Works on *nix systems."""

    path = None
    _has_lock = False

    def __init__(self, path):
        self.path = path

    def obtain(self):
        import os
        try:
            os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            self._has_lock = True
            print "Successfully obtained lock: %s" % self.path
        except OSError:
            return False
        else:
            return True

    def release(self):
        import os
        if not self._has_lock:
            raise Exception("Unable to release lock that is owned by another process")
        try:
            os.remove(self.path)
            print "Successfully released lock: %s" % self.path
        finally:
            self._has_lock = False

    def has_lock(self):
        return self._has_lock

    def clear(self):
        import os
        try:
            os.remove(self.path)
        except OSError:
            pass
        finally:
            print "Successfully cleared lock: %s" % self.path
            self._has_lock = False


class GitWrapper():
    """Wraps the git client. Currently uses git through shell command invocations."""

    def __init__(self):
        pass

    @staticmethod
    def pull(repo_config):
        """Pulls the latest version of the repo from the git server"""
        from subprocess import call

        branch = ('branch' in repo_config) and repo_config['branch'] or 'master'

        print "\nPost push request received"
        print 'Updating ' + repo_config['path']

        res = call(['sleep 5; cd "' +
                    repo_config['path'] +
                    '" && unset GIT_DIR && git fetch origin && git update-index --refresh && git reset --hard origin/' +
                    branch + ' && git submodule init && git submodule update'], shell=True)
        call(['echo "Pull result: ' + str(res) + '"'], shell=True)
        return res

    @staticmethod
    def clone(url, path):
        from subprocess import call
        call(['git clone --recursive %s %s' % (url, path)], shell=True)


    @staticmethod
    def deploy(repo_config):
        """Executes any supplied post-pull deploy command"""
        from subprocess import call

        path = repo_config['path']

        cmds = []
        if 'deploy' in repo_config:
            cmds.append(repo_config['deploy'])

        gd = GitAutoDeploy().get_config()['global_deploy']
        if len(gd[0]) is not 0:
            cmds.insert(0, gd[0])
        if len(gd[1]) is not 0:
            cmds.append(gd[1])

        print 'Executing deploy command(s)'

        for cmd in cmds:
            call(['cd "' + path + '" && ' + cmd], shell=True)


from BaseHTTPServer import BaseHTTPRequestHandler


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """Extends the BaseHTTPRequestHandler class and handles the incoming HTTP requests."""

    def do_POST(self):
        """Invoked on incoming POST requests"""
        from threading import Timer

        # Extract repository URL(s) from incoming request body
        repo_urls = self.get_repo_urls_from_request()

        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

        # Wait one second before we do git pull (why?)
        Timer(1.0, GitAutoDeploy.process_repo_urls, [repo_urls]).start()

    def get_repo_urls_from_request(self):
        """Parses the incoming request and extracts all possible URLs to the repository in question. Since repos can
        have both ssh://, git:// and https:// URIs, and we don't know which of them is specified in the config, we need
        to collect and compare them all."""
        import json

        #content_type = self.headers.getheader('content-type')
        length = int(self.headers.getheader('content-length'))
        body = self.rfile.read(length)

        data = json.loads(body)

        repo_urls = []

        gitlab_event = self.headers.getheader('X-Gitlab-Event')
        github_event = self.headers.getheader('X-GitHub-Event')
        user_agent = self.headers.getheader('User-Agent')

        if not 'repository' in data:
            print "ERROR - Unable to recognize data format"
            return repo_urls

        # Assume GitLab if the X-Gitlab-Event HTTP header is set
        if gitlab_event:

            print "Received '%s' event from GitLab" % gitlab_event

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_http_url', 'git_ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

        # Assume GitHub if the X-GitHub-Event HTTP header is set
        elif github_event:

            print "Received '%s' event from GitHub" % github_event

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_url', 'clone_url', 'ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

        # Assume BitBucket if the User-Agent HTTP header is set to 'Bitbucket-Webhooks/2.0' (or something similar)
        elif user_agent.lower().find('bitbucket') != -1:

            print "Received event from BitBucket"

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_url', 'clone_url', 'ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

            if 'full_name' in data['repository']:
                repo_urls.append('git@bitbucket.org:%s.git' % data['repository']['full_name'])
                repo_urls.append('https://oliverpoignant@bitbucket.org/%s.git' % data['repository']['full_name'])

        else:
            print "ERROR - Unable to recognize request origin. Don't know how to handle the request."

        return repo_urls


class GitAutoDeploy(object):

    config_path = './GitAutoDeploy.conf.json'
    debug = True
    daemon = False

    _instance = None
    _server = None
    _config = None

    def __new__(cls, *args, **kwargs):
        """Overload constructor to enable Singleton access"""
        if not cls._instance:
            cls._instance = super(GitAutoDeploy, cls).__new__(
                cls, *args, **kwargs)
        return cls._instance

    @staticmethod
    def debug_diagnosis(port):
        if GitAutoDeploy.debug is False:
            return

        pid = GitAutoDeploy.get_pid_on_port(port)
        if pid is False:
            print 'I don\'t know the number of pid that is using my configured port'
            return

        print 'Process with pid number %s is using port %s' % (pid, port)
        with open("/proc/%s/cmdline" % pid) as f:
            cmdline = f.readlines()
            print 'cmdline ->', cmdline[0].replace('\x00', ' ')

    @staticmethod
    def get_pid_on_port(port):
        import os

        with open("/proc/net/tcp",'r') as f:
            file_content = f.readlines()[1:]

        pids = [int(x) for x in os.listdir('/proc') if x.isdigit()]
        conf_port = str(port)
        mpid = False

        for line in file_content:
            if mpid is not False:
                break

            _, laddr, _, _, _, _, _, _, _, inode = line.split()[:10]
            decport = str(int(laddr.split(':')[1], 16))

            if decport != conf_port:
                continue

            for pid in pids:
                try:
                    path = "/proc/%s/fd" % pid
                    if os.access(path, os.R_OK) is False:
                        continue

                    for fd in os.listdir(path):
                        cinode = os.readlink("/proc/%s/fd/%s" % (pid, fd))
                        minode = cinode.split(":")

                        if len(minode) == 2 and minode[1][1:-1] == inode:
                            mpid = pid

                except Exception as e:
                    pass

        return mpid

    @staticmethod
    def process_repo_urls(urls):
        import os
        import time

        repo_config = GitAutoDeploy().get_matching_repo_config(urls)

        if not repo_config:
            print 'Unable to find any of the repository URLs in the config: %s' % ', '.join(urls)
            return

        running_lock = Lock(os.path.join(repo_config['path'], 'status_running'))
        waiting_lock = Lock(os.path.join(repo_config['path'], 'status_waiting'))
        try:

            # Attempt to obtain the status_running lock
            while not running_lock.obtain():

                # If we're unable, try once to obtain the status_waiting lock
                if not waiting_lock.has_lock() and not waiting_lock.obtain():
                    print "Unable to obtain the status_running lock nor the status_waiting lock. Another process is "\
                          + "already waiting, so we'll ignore the request."

                    # If we're unable to obtain the waiting lock, ignore the request
                    return

                # Keep on attempting to obtain the status_running lock until we succeed
                time.sleep(5)

            n = 4
            while 0 < n and 0 != GitWrapper.pull(repo_config):
                n -= 1
            if 0 < n:
                GitWrapper.deploy(repo_config)

        except Exception as e:
            print 'Error during \'pull\' or \'deploy\' operation on path: %s' % repo_config['path']
            print e

        finally:

            # Release the lock if it's ours
            if running_lock.has_lock():
                running_lock.release()

            # Release the lock if it's ours
            if waiting_lock.has_lock():
                waiting_lock.release()

    def get_config(self):
        import json
        import sys
        import os

        if self._config:
            return self._config

        try:
            config_string = open(self.config_path).read()

        except Exception as e:
            print "Could not load %s file\n" % self.config_path
            raise e

        try:
            self._config = json.loads(config_string)

        except Exception as e:
            print "%s file is not valid JSON\n" % self.config_path
            raise e

        # Translate any ~ in the path into /home/<user>
        if 'pidfilepath' in self._config:
            self._config['pidfilepath'] = os.path.expanduser(self._config['pidfilepath'])

        for repo_config in self._config['repositories']:

            # Translate any ~ in the path into /home/<user>
            if 'path' in repo_config:
                repo_config['path'] = os.path.expanduser(repo_config['path'])

            if not os.path.isdir(repo_config['path']):

                print "Directory %s not found" % repo_config['path']
                GitWrapper.clone(url=repo_config['url'], path=repo_config['path'])

                if not os.path.isdir(repo_config['path']):
                    print "Unable to clone repository %s" % repo_config['url']
                    sys.exit(2)

                else:
                    print "Repository %s successfully cloned" % repo_config['url']

            if not os.path.isdir(repo_config['path'] + '/.git'):
                print "Directory %s is not a Git repository" % repo_config['path']
                sys.exit(2)

            # Clear any existing lock files, with no regard to possible ongoing processes
            Lock(os.path.join(repo_config['path'], 'status_running')).clear()
            Lock(os.path.join(repo_config['path'], 'status_waiting')).clear()

        return self._config

    def get_matching_repo_config(self, urls):
        """Iterates over the various repo URLs provided as argument (git://, ssh:// and https:// for the repo) and
        compare them to any repo URL specified in the config"""

        config = self.get_config()

        for url in urls:
            for repo_config in config['repositories']:
                if repo_config['url'] == url:
                    return repo_config

    def ssh_key_scan(self):
        import re
        from subprocess import call

        for repository in self.get_config()['repositories']:

            url = repository['url']
            print "Scanning repository: %s" % url
            m = re.match('.*@(.*?):', url)

            if m is not None:
                port = repository['port']
                port = '' if port is None else ('-p' + port)
                call(['ssh-keyscan -t ecdsa,rsa ' + port + ' ' + m.group(1) + ' >> $HOME/.ssh/known_hosts'], shell=True)

            else:
                print 'Could not find regexp match in path: %s' % url

    def kill_conflicting_processes(self):
        import os

        pid = GitAutoDeploy.get_pid_on_port(self.get_config()['port'])

        if pid is False:
            print '[KILLER MODE] I don\'t know the number of pid that is using my configured port\n ' \
                '[KILLER MODE] Maybe no one? Please, use --force option carefully'
            return False

        os.kill(pid, signal.SIGKILL)
        return True

    def create_pid_file(self):
        import os
        with open(self.get_config()['pidfilepath'], 'w') as f:
            f.write(str(os.getpid()))

    def read_pid_file(self):
        with open(self.get_config()['pidfilepath'], 'r') as f:
            return f.readlines()

    def remove_pid_file(self):
        import os
        os.remove(self.get_config()['pidfilepath'])

    def exit(self):
        import sys
        print '\nGoodbye'
        self.remove_pid_file()
        sys.exit(0)

    def run(self):
        from sys import argv
        import sys
        from BaseHTTPServer import HTTPServer
        import socket

        if '-d' in argv or '--daemon-mode' in argv:
            self.daemon = True

        if '-q' in argv or '--quiet' in argv:
            sys.stdout = open(os.devnull, 'w')

        if '--ssh-keygen' in argv:
            print 'Scanning repository hosts for ssh keys...'
            self.ssh_key_scan()

        if '--force' in argv:
            print 'Attempting to kill any other process currently occupying port %s' % self.get_config()['port']
            self.kill_conflicting_processes()

        if '--config' in argv:
            import os
            pos = argv.index('--config')
            if len(argv) > pos + 1:
                self.config_path = os.path.realpath(argv[argv.index('--config') + 1])
                print 'Using custom configuration file \'%s\'' % self.config_path

        if GitAutoDeploy.daemon:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
            os.setsid()

        self.create_pid_file()

        if self.daemon:
            print 'GitHub & GitLab auto deploy service v 0.1 started in daemon mode'

            # Disable output in daemon mode
            sys.stdout = open(os.devnull, 'w')

        else:
            print 'GitHub & GitLab auto deploy service v 0.1 started'

        try:
            self._server = HTTPServer((self.get_config()['host'], self.get_config()['port']), WebhookRequestHandler)
            sa = self._server.socket.getsockname()
            print "Listening on", sa[0], "port", sa[1]
            self._server.serve_forever()

        except socket.error, e:

            if not GitAutoDeploy.daemon:
                print "Error on socket: %s" % e
                GitAutoDeploy.debug_diagnosis(self.get_config()['port'])

            sys.exit(1)

    def stop(self):
        if self._server is not None:
            self._server.socket.close()

    def signal_handler(self, signum, frame):
        self.stop()

        if signum == 1:
            self.run()
            return

        elif signum == 2:
            print '\nRequested close by keyboard interrupt signal'

        elif signum == 6:
            print 'Requested close by SIGABRT (process abort signal). Code 6.'

        self.exit()

if __name__ == '__main__':
    import signal

    app = GitAutoDeploy()

    signal.signal(signal.SIGHUP, app.signal_handler)
    signal.signal(signal.SIGINT, app.signal_handler)
    signal.signal(signal.SIGABRT, app.signal_handler)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    app.run()