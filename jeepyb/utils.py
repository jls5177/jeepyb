# Copyright (c) 2013 Mirantis.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import ConfigParser
import logging
import os
import shutil
import subprocess
import stat
import sys
import tempfile
import yaml

PROJECTS_INI = os.environ.get('PROJECTS_INI', '/home/gerrit2/projects.ini')
PROJECTS_YAML = os.environ.get('PROJECTS_YAML', '/home/gerrit2/projects.yaml')

log = logging.getLogger("jeepyb.utils")


DEBUG_GIT = False

def short_project_name(full_project_name):
    """Return the project part of the git repository name."""
    return full_project_name.split('/')[-1]


def run_command(cmd, status=False, env=None):
    env = env or {}
    newenv = os.environ
    if DEBUG_GIT:
        newenv['GIT_TRACE'] = '1'
        newenv['GIT_CURL_VERBOSE'] = '1'
    if not is_windows():
        newenv.update(env)
    log.info("Executing command: %s" % " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=newenv)
    (out, nothing) = p.communicate()
    log.debug("Return code: %s" % p.returncode)
    log.debug("Command said: %s" % out.strip())
    if status:
        return p.returncode, out.strip()
    return out.strip()


def run_command_status(cmd, env=None):
    env = env or {}
    return run_command(cmd, True, env)


def git_command(repo_dir, sub_cmd, env=None):
    return git_command_output(repo_dir, sub_cmd, env, output=False)


def git_command_output(repo_dir, sub_cmd, env=None, output=True):
    env = env or {}
    git_dir = os.path.join(repo_dir, '.git')
    cmd = ['git', '--git-dir=%s' % git_dir, '--work-tree=%s' % repo_dir] + list(sub_cmd)
    status, out = run_command(cmd, True, env)
    # Print output if there was an error
    if len(out):
        logger = log.info if status == 0 else log.warning
        logger('Output:\n%s' % out)
    if output:
        return status, out
    return status


def make_ssh_wrapper(gerrit_user, gerrit_key):
    (fd, name) = tempfile.mkstemp(text=True)
    os.write(fd, '#!/bin/bash\n')
    os.write(fd,
             'ssh -i %s -l %s -o "StrictHostKeyChecking no" -v $@\n' %
             (gerrit_key.replace('\\', '\\\\'), gerrit_user))
    os.close(fd)
    os.chmod(name, 0o755)
    return dict(GIT_SSH=name)


class ProjectsRegistry(object):
    """read config from ini or yaml file.

    It could be used as dict 'project name' -> 'project properties'.
    """

    def __init__(self, yaml_file=None, single_doc=True):
        yaml_file = yaml_file or PROJECTS_YAML
        self.yaml_doc = [c for c in yaml.safe_load_all(open(yaml_file))]
        self.single_doc = single_doc

        self.configs_list = []
        self.defaults = {}
        self._parse_file()

    def _parse_file(self):
        if self.single_doc:
            self.configs_list = self.yaml_doc[0]
        else:
            self.configs_list = self.yaml_doc[1]

        if os.path.exists(PROJECTS_INI):
            self.defaults = ConfigParser.ConfigParser()
            self.defaults.read(PROJECTS_INI)
        else:
            try:
                self.defaults = self.yaml_doc[0][0]
            except IndexError:
                pass

        configs = {}
        for section in self.configs_list:
            configs[section['project']] = section

        self.configs = configs

    def __getitem__(self, item):
        return self.configs[item]

    def get_project_item(self, project, item, default=None):
        if project in self.configs:
            return self.configs[project].get(item, default)
        else:
            return default

    def get(self, item, default=None):
        return self.configs.get(item, default)

    def get_defaults(self, item, default=None):
        if os.path.exists(PROJECTS_INI):
            section = 'projects'
            if self.defaults.has_option(section, item):
                if type(default) == bool:
                    return self.defaults.getboolean(section, item)
                else:
                    return self.defaults.get(section, item)
            return default
        else:
            return self.defaults.get(item, default)


def set_project_config_dir(directory, yaml_name='projects.yaml', ini_name='projects.ini'):
    """
    Override the default Project Config directory
    :param directory: The root directory with the configuration files
    :param yaml_name: the name of the project config file to load
    :param ini_name: the name of the Jeepyb settings file to load
    """
    global PROJECTS_YAML, PROJECTS_INI
    if not os.path.exists(directory):
        return
    PROJECTS_YAML = os.path.join(directory, yaml_name)
    PROJECTS_INI = os.path.join(directory, ini_name)


def is_windows():
    if sys.platform.startswith('win'):
        return True
    return False


def is_cygwin():
    if sys.platform.startswith('cygwin'):
        return True
    return False


def onerror(func, path, exc_info):
    """
    Error handler for ``shutil.rmtree``.

    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.

    If the error is for another reason it re-raises the error.
    
    Taken from Stack Over:
    http://stackoverflow.com/a/2656405/1950131
    
    Usage : ``shutil.rmtree(path, onerror=onerror)``
    """
    if not os.access(path, os.W_OK):
        # Is the error an access error ?
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise


def remove_dir_if_exists(path):
    """
    Checks if path exists and deletes it
    :param path: path to delete
    """
    if os.path.exists(path):
        shutil.rmtree(path, onerror=onerror)


def fixup_path(path, fix_cygwin_path=False):
    if is_windows() or (fix_cygwin_path and is_cygwin()):
        return path.replace('/', '\\')
    return path

