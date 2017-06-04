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
import os
import time

import gerritlib.gerrit as GerritSshAPI
from . import gerritrestapi as GerritRestAPI

from jeepyb.utils import run_command, \
    run_command_status, git_command, \
    make_ssh_wrapper, git_command_output, \
    is_windows, remove_dir_if_exists


log = logging.getLogger(__name__)

# Gerrit system groups as defined:
# https://review.openstack.org/Documentation/access-control.html#system_groups
# Need to set Gerrit system group's uuid to the format it expects.
GERRIT_SYSTEM_GROUPS = {
    'Anonymous Users': 'global:Anonymous-Users',
    'Project Owners': 'global:Project-Owners',
    'Registered Users': 'global:Registered-Users',
    'Change Owner': 'global:Change-Owner',
}

GITREVIEW_TEMPLATE = """[gerrit]
host=%s
port=%s
project=%s
"""


class FetchConfigException(Exception):
    pass


class GerritAPI(object):

    def __init__(self, host, user, port, ssh_key, url, http_pass, gitid, system_user, system_group):
        self.host = host
        self.user = user
        self.port = port
        self.ssh_key = ssh_key
        self.url = url
        self.http_pass = http_pass
        self.gitid = gitid
        self.system_user = system_user
        self.system_group = system_group

        self._gerrit_ssh = GerritSshAPI.Gerrit(hostname=host,
                                               username=user,
                                               port=port,
                                               keyfile=ssh_key)
        self._gerrit_rest = GerritRestAPI.GerritRestApi(host=url,
                                                        username=user,
                                                        password=http_pass)

        self.ssh_env = make_ssh_wrapper(self.user, self.ssh_key)

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.host)

    @property
    def projects(self):
        return self._gerrit_ssh.listProjects()

    def get_remote_url(self, project, use_ssh=True):
        if use_ssh:
            remote_url = "ssh://%s@%s:%s/%s" % (
                self.user,
                self.host,
                self.port,
                project)
        else:
            # todo: fill in HTTP url
            remote_url = ''
        return remote_url

    def create_project(self, name, description, is_parent, parent_project):
        if name in self.projects:
            return False
        return self._gerrit_ssh.createProject(name,
                                              description=description,
                                              is_parent=is_parent,
                                              parent_project=parent_project)

    def replicate(self, project_name):
        self._gerrit_ssh.replicate(project_name)

    @property
    def groups_dict(self):
        group_column_names = ['name', 'uuid', 'desc', 'owner', 'owner_uuid', 'visible_to_all']
        all_groups = self._gerrit_ssh.listGroups(verbose=True)
        if len(all_groups) == 0:
            return dict()

        groups = dict()
        for group in all_groups:
            group_dict = dict((n, v) for n, v in zip(group_column_names, group.split('\t', len(group_column_names))))
            groups[group_dict['name']] = group_dict
        return groups

    def create_group(self, name, description=None, members=None, subgroups=None, ldap_groups=None, visible_to_all=True,
                     owner=None):
        groups = self.groups_dict
        members = members or []
        subgroups = subgroups or []
        ldap_groups = ldap_groups or []

        # Create the group if it does not exist
        if name not in groups:
            self._gerrit_ssh.createGroup(group=name,
                                         description=description,
                                         visible_to_all=visible_to_all,
                                         owner=owner)

        # Add any direct group members
        if len(members):
            self._gerrit_rest.add_group_members(group=name, members=members)

        # Add any Internal sub-groups
        if len(subgroups):
            self._gerrit_rest.add_internal_include_groups(group=name,
                                                          include_groups=subgroups)

        # Add any LDAP Groups
        if len(ldap_groups):
            self._gerrit_rest.add_include_groups(group=name, include_groups=ldap_groups)

    def _get_group_uuid(self, group, retries=10):
        """
        Returns the UUID for the group if found in the gerrit server
        Wait for up to 10 seconds for the group to be created in the DB.
        """
        for x in range(retries):
            all_groups = self.groups_dict
            if group in all_groups:
                return all_groups[group]['uuid']
            if retries > 1:
                time.sleep(1)
        return None

    def group_uuid(self, group_name):
        uuid = self._get_group_uuid(group_name, retries=1)
        if uuid:
            return uuid
        if group_name in GERRIT_SYSTEM_GROUPS:
            return GERRIT_SYSTEM_GROUPS[group_name]
        self.create_group(name=group_name)
        uuid = self._get_group_uuid(group_name)
        if uuid:
            return uuid
        return None

    def meta_updater(self, project, checkout_path):
        use_ssh = True  # todo: add support to use HTTP
        return GerritMetaUpdater(project=project,
                                 remote_url=self.get_remote_url(project, use_ssh=use_ssh),
                                 checkout_path=checkout_path,
                                 gerrit_api=self)


class GerritMetaUpdater(object):

    def __init__(self, project, remote_url, checkout_path, gerrit_api):
        """
        :param project: 
        :param remote_url: 
        :param checkout_path: 
        :param gerrit_api: 
        :type project: str
        :type remote_url: str
        :type checkout_path: str
        :type gerrit_api: GerritAPI
        """
        self.project = project
        self.remote_url = remote_url
        self.checkout_path = checkout_path
        self._gerrit_api = gerrit_api
        self.current_head = 'master'

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.project)

    def __enter__(self):
        st, out = git_command_output(self.checkout_path, ['rev-parse', '--abbrev-ref', 'HEAD'])
        if st == 0 and out:
            log.info('Current branch is %s' % self.current_head)
            self.current_head = out
        self.fetch_meta_config()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            log.exception(
                "Exception processing ACLS for %s." % self.project)

        self.push_meta_config()

        # Reset git checkout before exiting
        git_command(self.checkout_path, ['reset', '--hard'])
        git_command(self.checkout_path, ['checkout', self.current_head])
        git_command(self.checkout_path, ['branch', '-D', 'config'])

        return True

    def fetch_meta_config(self):
        """
        Fetches the refs/meta/config ref from the remote repo
        """
        env = self._gerrit_api.ssh_env or {}

        # Poll for refs/meta/config as gerrit may not have written it out for
        # us yet.
        for x in range(10):
            cmd = ['fetch', self.remote_url, '+refs/meta/config:refs/remotes/gerrit-meta/config']
            status = git_command(self.checkout_path, cmd, env)
            if status == 0:
                break
            else:
                log.debug("Failed to fetch refs/meta/config for project: %s" %
                          self.project)
                time.sleep(2)
        else:
            log.error("Failed to fetch refs/meta/config for project: %s" % self.project)
            raise FetchConfigException()

        # Poll for project.config as gerrit may not have committed an empty
        # one yet.
        output = ""
        for x in range(10):
            status = git_command(self.checkout_path, ['remote', 'update', '--prune'], env)
            if status != 0:
                log.error("Failed to update remote: %s" % self.remote_url)
                time.sleep(2)
                continue
            else:
                cmd = ['ls-files', '--with-tree=remotes/gerrit-meta/config', 'project.config']
                status, output = git_command_output(self.checkout_path,cmd, env)
            if "project.config" not in output.strip() or status != 0:
                log.debug("Failed to find project.config for project: %s" %
                          self.project)
                time.sleep(2)
            else:
                break
        else:
            log.error("Failed to find project.config for project: %s" % self.project)
            raise FetchConfigException()

        # Because the following fails if executed more than once you should only
        # run fetch_meta_config once in each repo.
        status = git_command(self.checkout_path, ['checkout', '-B', 'config', 'remotes/gerrit-meta/config'])
        if status != 0:
            log.error("Failed to checkout config for project: %s" % self.project)
            raise FetchConfigException()

    def push_meta_config(self):
        """
        Pushes the modified refs/meta/config back to the remote repo
        :return: True if push was successful, False if an error occurred
        """
        env = self._gerrit_api.ssh_env or {}

        # Check for changes before we try to push
        status = git_command(self.checkout_path, ['diff-index', '--quiet', 'HEAD', '--'])
        if status == 0:
            log.info('No changes to push')
            return

        cmd = ['commit', '-a', '-m "Update project config."', '--author="%s"' % self._gerrit_api.gitid]
        status = git_command(self.checkout_path, cmd)
        if status != 0:
            log.error("Failed to commit config for project: %s" % self.project)
            return False
        cmd = ['push', self.remote_url, 'HEAD:refs/meta/config']
        status = git_command(self.checkout_path, cmd, env)
        if status != 0:
            log.error("Failed to push config for project: %s" % self.project)
            return False
        return True


class GerritCheckout(object):

    def __init__(self, project, checkout_path, upstream, gerrit_api):
        """
        :param project: 
        :param checkout_path: 
        :param gerrit_api: 
        :type project: str
        :type checkout_path: str
        :type gerrit_api: GerritAPI
        """
        self.project = project
        self.checkout_path = checkout_path
        self.upstream = upstream
        self._gerrit_api = gerrit_api

        use_ssh = True  # todo: add support to use HTTP
        self.remote_url = self._gerrit_api.get_remote_url(project, use_ssh=use_ssh)

        self.ssh_env = self._gerrit_api.ssh_env
        self.project_git = '%s.git' % self.project

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.project)

    def change_user(self, gitid=None):
        gitid = gitid or self._gerrit_api.gitid
        user, email = gitid.rsplit(" <", 2)

        # trim trailing '>' character off of the email
        email = email.rstrip('>')

        log.info('Changing Author, username=%s, email=%s' % (user, email))
        git_command(self.checkout_path, ['config', 'user.name', user], env=self.ssh_env)
        git_command(self.checkout_path, ['config', 'user.email', email], env=self.ssh_env)

    def make_local_copy(self):
        # Ensure that the base location exists
        if not os.path.exists(os.path.dirname(self.checkout_path)):
            os.makedirs(os.path.dirname(self.checkout_path))

        # Three choices
        #  - If gerrit has it, get from gerrit
        #  - If gerrit doesn't have it:
        #    - If it has an upstream, clone that
        #    - If it doesn't, create it

        # Gerrit knows about the project, clone it
        # TODO(mordred): there is a possible failure condition here
        #                we should consider 'gerrit has it' to be
        #                'gerrit repo has a master branch'
        if self.project in self._gerrit_api.projects:
            try:
                cmd = ['git', 'clone', self.remote_url, self.checkout_path]
                run_command(cmd, env=self.ssh_env)
                if self.upstream:
                    git_command(self.checkout_path, ['remote', 'add', '-f', 'upstream', self.upstream])
                self.change_user()
                return None
            except Exception:
                # If the clone fails, then we need to clone from the upstream
                # source
                pass

        # Gerrit doesn't have it, but it has an upstream configured
        # We're probably importing it for the first time, clone
        # upstream, but then ongoing we want gerrit to ge origin
        # and upstream to be only there for ongoing tracking
        # purposes, so rename origin to upstream and add a new
        # origin remote that points at gerrit
        if self.upstream:
            run_command(['git', 'clone', self.upstream, self.checkout_path], env=self.ssh_env)
            git_command(self.checkout_path, ['fetch', 'origin', '+refs/heads/*:refs/copy/heads/*'], env=self.ssh_env)
            git_command(self.checkout_path, ['remote', 'rename', 'origin', 'upstream'])
            git_command(self.checkout_path, ['remote', 'add', 'origin', self.remote_url])
            self.change_user()
            return 'push %s +refs/copy/heads/*:refs/heads/*'

        # Neither gerrit has it, nor does it have an upstream,
        # just create a whole new one
        run_command(['git', 'init', self.checkout_path])
        self.change_user()

        git_command(self.checkout_path, ['remote', 'add', 'origin', self.remote_url])
        with open(os.path.join(self.checkout_path, ".gitreview"), 'w') as gitreview:
            gitreview.write(GITREVIEW_TEMPLATE %
                            (self._gerrit_api.host, self._gerrit_api.port, self.project_git))
        git_command(self.checkout_path, ['add', '.gitreview'])

        cmd = ['commit', '-a', '-m "Added .gitreview"', '--author="%s"' % self._gerrit_api.gitid]
        git_command(self.checkout_path, cmd)

        return "push %s HEAD:refs/heads/master"

    def sync_upstream(self, upstream_prefix=None):
        git_command(self.checkout_path, ['remote', 'update', 'upstream', '--prune'], env=self.ssh_env)
        # Any branch that exists in the upstream remote, we want
        # a local branch of, optionally prefixed with the
        # upstream prefix value
        branches = git_command_output(self.checkout_path, ['branch', '-a'])[1].split('\n')
        for branch in branches:
            branch = branch.strip()
            if not branch.startswith("remotes/upstream"):
                continue
            if "->" in branch:
                continue
            local_branch = branch.split()[0][len('remotes/upstream/'):]
            if upstream_prefix:
                local_branch = "%s/%s" % (upstream_prefix, local_branch)

            # Check out an up to date copy of the branch, so that
            # we can push it and it will get picked up below
            git_command(self.checkout_path, ['checkout', '-B', '%s' % local_branch, branch])

        try:
            # Push all of the local branches to similarly named
            # Branches on gerrit. Also, push all of the tags
            cmd = ['push', 'origin', 'refs/heads/*:refs/heads/*']
            git_command(self.checkout_path, cmd, env=self.ssh_env)
            git_command(self.checkout_path, ['push', 'origin', '--tags'], env=self.ssh_env)
        except Exception:
            log.exception(
                "Error pushing %s to Gerrit." % self.project)

    def push_to_gerrit(self, push_string):
        try:
            git_command(self.checkout_path, push_string % self.remote_url, env=self.ssh_env)
            git_command(self.checkout_path, ['push', '--tags', self.remote_url], env=self.ssh_env)
        except Exception:
            log.exception(
                "Error pushing %s to Gerrit." % self.project)

    def fsck_repo(self):
        rc, out = git_command_output(self.checkout_path, ['fsck', '--full'])
        # Check for non zero return code or warnings which should
        # be treated as errors. In this case zeroPaddedFilemodes
        # will not be accepted by Gerrit/jgit but are accepted by C git.
        if rc != 0 or 'zeroPaddedFilemode' in out:
            log.error('git fsck of %s failed:\n%s' % (self.checkout_path, out))
            raise Exception('git fsck failed not importing')

    def create_local_mirror(self, local_git_dir):
        """
        Creates a bare git repo if it does not already exist (that's it)
        :param local_git_dir: 
        """
        git_mirror_path = os.path.join(local_git_dir, self.project_git)
        if os.path.exists(git_mirror_path):
            return

        (ret, output) = run_command_status(['git', '--bare', 'init', git_mirror_path])
        if ret:
            remove_dir_if_exists(git_mirror_path)
            raise Exception(output)

        if not is_windows():
            cmd = ['chown', '-R',
                   '%s:%s' % (self._gerrit_api.system_user, self._gerrit_api.system_group), git_mirror_path]
            run_command(cmd)

    def update_local_copy(self, track_upstream):
        # first do a clean of the branch to prevent possible
        # problems due to previous runs
        git_command(self.checkout_path, ['clean', '-fdx'])

        _, out = git_command_output(self.checkout_path, ['remote'])
        # todo: evaluate if 'upstream' is the correct prefix
        has_upstream_remote = 'upstream' in out

        if track_upstream:
            # If we're configured to track upstream but the repo
            # does not have an upstream remote, add one
            if not has_upstream_remote:
                cmd = ['remote', 'add', 'upstream', self.upstream]
                git_command(self.checkout_path, cmd)

            # If we're configured to track upstream, make sure that
            # the upstream URL matches the config
            else:
                cmd = ['remote', 'set-url', 'upstream', self.upstream]
                git_command(self.checkout_path, cmd)

            # Now that we have any upstreams configured, fetch all of the refs
            # we might need, pruning remote branches that no longer exist
            git_command(self.checkout_path, ['remote', 'update', '--prune'], env=self.ssh_env)
        else:
            # If we are not tracking upstream, then we do not need
            # an upstream remote configured
            if has_upstream_remote:
                git_command(self.checkout_path, ['remote', 'rm', 'upstream'])

        # Get Remote HEAD branch name (default to origin/master if it fails
        st, out = git_command_output(self.checkout_path, ['rev-parse', '--abbrev-ref', 'origin/HEAD'])
        origin_head = out if st == 0 and out else 'origin/master'

        # Local branch is just the remote branch name
        local_branch = origin_head.replace('origin/', '')

        # Checkout master and reset to the state of the origin branch
        git_command(self.checkout_path, ['checkout', '-B', local_branch, origin_head])

