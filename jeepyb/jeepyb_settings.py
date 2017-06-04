# Copyright (c) 2017 Justin Simon.
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
import logging
import json
import os

import shutil

import jeepyb.utils as u

log = logging.getLogger(__name__)


class JeepybSettings(object):
    def __init__(self, project_cfg_dir=None, update_cache=True, cleanup_tmp=True):
        # Set the project-config base directory if passed in as an argument
        self.project_cfg_dir = project_cfg_dir
        u.set_project_config_dir(project_cfg_dir or '')

        self._registry = u.ProjectsRegistry()

        self.project_cache = {}
        if os.path.exists(self.project_cache_file):
            self.project_cache = json.loads(open(self.project_cache_file, 'r').read())

        self.ssh_env = u.make_ssh_wrapper(self.gerrit_user, self.gerrit_key)

        self.update_cache = update_cache
        self.cleanup_tmp = cleanup_tmp

    def __repr__(self):
        return "%s" % self.__class__.__name__

    @property
    def default_has_github(self):
        return self._registry.get_defaults('has-github', True)

    @property
    def local_git_dir(self):
        return self._registry.get_defaults('local-git-dir', '/var/lib/git')

    @property
    def jeepyb_cache_dir(self):
        return self._registry.get_defaults('jeepyb-cache-dir', '/var/lib/jeepyb')

    @property
    def acl_dir(self):
        acl_dir = self._registry.get_defaults('acl-dir', 'acls')
        return os.path.join(self.project_cfg_dir, acl_dir)

    @property
    def group_dir(self):
        group_dir = self._registry.get_defaults('group-dir', 'groups')
        return os.path.join(self.project_cfg_dir, group_dir)

    @property
    def prolog_dir(self):
        prolog_dir = self._registry.get_defaults('prolog-dir', 'prolog')
        return os.path.join(self.project_cfg_dir, prolog_dir)

    @property
    def gerrit_host(self):
        return self._registry.get_defaults('gerrit-host')

    @property
    def gerrit_url(self):
        return self._registry.get_defaults('gerrit-url')

    @property
    def gitreview_gerrit_host(self):
        return self._registry.get_defaults(
            'gitreview-gerrit-host', self.gerrit_host)

    @property
    def gerrit_port(self):
        return int(self._registry.get_defaults('gerrit-port', '29418'))

    @property
    def gitreview_gerrit_port(self):
        return int(self._registry.get_defaults(
            'gitreview-gerrit-port', self.gerrit_port))

    @property
    def gerrit_user(self):
        return self._registry.get_defaults('gerrit-user')

    @property
    def gerrit_http_pass(self):
        return self._registry.get_defaults('gerrit-http-pass')

    @property
    def gerrit_key(self):
        return self._registry.get_defaults('gerrit-key')

    @property
    def gerrit_gitid(self):
        return self._registry.get_defaults('gerrit-committer')

    @property
    def gerrit_replicate(self):
        return self._registry.get_defaults('gerrit-replicate', True)

    @property
    def gerrit_os_system_user(self):
        return self._registry.get_defaults('gerrit-system-user',
                                           'gerrit2')

    @property
    def gerrit_os_system_group(self):
        return self._registry.get_defaults('gerrit-system-group',
                                           'gerrit2')

    @property
    def default_homepage(self):
        return self._registry.get_defaults('homepage')

    @property
    def default_has_issues(self):
        return self._registry.get_defaults('has-issues', False)

    @property
    def default_has_downloads(self):
        return self._registry.get_defaults('has-downloads', False)

    @property
    def default_has_wiki(self):
        return self._registry.get_defaults('has-wiki', False)

    @property
    def github_secure_config(self):
        return self._registry.get_defaults(
            'github-config',
            '/etc/github/github-projects.secure.config')

    @property
    def project_cache_file(self):
        return os.path.join(self.jeepyb_cache_dir, 'project.cache')

    @property
    def project_config(self):
        return JeepybProjectConfigList(self._registry.configs_list, self, cleanup_tmp=self.cleanup_tmp)

    def __enter__(self):
        return self.project_config

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            log.exception(
                "Encountered problems during execution, exiting.")

        # Only save the cache if we are allowed to
        if self.update_cache:
            with open(self.project_cache_file, 'w') as cache_out:
                log.info("Writing cache file %s", self.project_cache_file)
                cache_out.write(json.dumps(
                    self.project_cache, sort_keys=True, indent=2))

        os.unlink(self.ssh_env['GIT_SSH'])


class JeepybProjectConfigList(object):

    def __init__(self, configs_list, jeepyb_settings, cleanup_tmp=True):
        self.configs_list = configs_list
        self.jeepyb_settings = jeepyb_settings
        self.cleanup_tmp = cleanup_tmp

    def __repr__(self):
        return "%s" % self.__class__.__name__

    def __len__(self):
        return len(self.configs_list)

    def __getitem__(self, index):
        """
        Get the next project config from the list
        :param index: the project config index to return
        :type index: int 
        :return: a Project Configuration object
        :rtype: JeepybProjectConfig
        """
        if index >= len(self.configs_list):
            raise IndexError
        return JeepybProjectConfig(self.configs_list[index], self.jeepyb_settings, cleanup_tmp=self.cleanup_tmp)


class JeepybProjectConfig(object):

    def __init__(self, project_config, jeepyb_settings, cleanup_tmp=True):
        """
        
        :param project_config (dict): Project Configuration defined in file
        :param jeepyb_settings (JeepybSettings): Parsed Jeepyb Settings
        """
        self.config = project_config
        self.jeepyb_settings = jeepyb_settings
        self.cleanup_tmp = cleanup_tmp

        # Create project cache if it was not already present
        self.jeepyb_settings.project_cache.setdefault(self.project_name, {})

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.project_name)

    @property
    def cache(self):
        return self.jeepyb_settings.project_cache.get(self.project_name, {})

    @property
    def already_created(self):
        return self.cache.get('project-created', False)

    @property
    def project_name(self):
        return self.config['project']

    @property
    def options(self):
        return self.config.get('options', dict())

    @property
    def no_gerrit(self):
        return 'no-gerrit' in self.options

    @property
    def track_upstream(self):
        return 'track-upstream' in self.options

    @property
    def description(self):
        return self.config.get('description', None)

    @description.setter
    def description(self, value):
        self.config['description'] = value

    @property
    def upstream(self):
        return self.config.get('upstream', None)

    @property
    def upstream_prefix(self):
        return self.config.get('upstream-prefix', None)

    @property
    def is_parent(self):
        return self.config.get('is-parent', None)

    @property
    def parent_project(self):
        return self.config.get('parent-project', None)

    @property
    def acl_config(self):
        return self.config.get('acl-config',
                               '%s.config' % os.path.join(self.jeepyb_settings.acl_dir, self.project_name))

    @property
    def groups(self):
        return self.config.get('groups', None)

    @property
    def prolog_rule(self):
        return self.config.get('prolog-rule', None)

    @property
    def homepage(self):
        return self.config.get('homepage', self.jeepyb_settings.default_homepage)

    @property
    def cache_pushed_to_gerrit(self):
        return self.cache.get('pushed-to-gerrit', None)

    @property
    def cache_pushed_to_gerrit(self):
        return self.cache.get('pushed-to-gerrit', None)

    @property
    def repo_path(self):
        return os.path.abspath(os.path.join(self.jeepyb_settings.jeepyb_cache_dir, self.project_name))

    def __enter__(self):
        log.info("Processing project: %s" % self.project_name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        log.info("Finished processing %s" % self.project_name)
        if exc_type:
            log.exception(
                "Problems creating %s, moving on." % self.project_name)

        # Clean up after ourselves - this repo has no use
        if self.cleanup_tmp:
            u.remove_dir_if_exists(self.repo_path)

        return True

