#! /usr/bin/env python
# Copyright (C) 2011 OpenStack, LLC.
# Copyright (c) 2012 Hewlett-Packard Development Company, L.P.
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

# manage_projects.py reads a config file called projects.ini
# It should look like:

# [projects]
# homepage=http://openstack.org
# gerrit-host=review.openstack.org
# local-git-dir=/var/lib/git
# gerrit-key=/home/gerrit2/review_site/etc/ssh_host_rsa_key
# gerrit-committer=Project Creator <openstack-infra@lists.openstack.org>
# gerrit-replicate=True
# has-github=True
# has-wiki=False
# has-issues=False
# has-downloads=False
# acl-dir=/home/gerrit2/acls
# acl-base=/home/gerrit2/acls/project.config
#
# manage_projects.py reads a project listing file called projects.yaml
# It should look like:
# - project: PROJECT_NAME
#   options:
#    - has-wiki
#    - has-issues
#    - has-downloads
#    - has-pull-requests
#    - track-upstream
#   homepage: Some homepage that isn't http://openstack.org
#   description: This is a great project
#   upstream: https://gerrit.googlesource.com/gerrit
#   upstream-prefix: upstream
#   acl-config: /path/to/gerrit/project.config
#   acl-append:
#     - /path/to/gerrit/project.config
#   acl-parameters:
#     project: OTHER_PROJECT_NAME

import ConfigParser
import argparse
import glob
import hashlib
import logging
import os
import re
import shutil

import github
import yaml

from jeepyb.gerrit import GerritAPI, GerritCheckout
from jeepyb.jeepyb_settings import JeepybSettings, JeepybProjectConfig
import jeepyb.log as l
import jeepyb.utils as u

log = logging.getLogger("manage_projects")
orgs = None


class CopyFileException(Exception):
    pass


class CreateGroupException(Exception):
    pass


def copy_file_to_git_repo(repo_path, copy_file, dest_filename):
    """
    Copy given file to destination path
    :param repo_path: Path to git repo
    :param copy_file: File to copy
    :param dest_filename: Destination (relative to repo_path) to copy file to
    :return: True if the repo is dirty (file was updated in the copy), False if clean
    """
    if not os.path.exists(copy_file):
        raise CopyFileException()

    dest_path = os.path.join(repo_path, dest_filename)
    shutil.copy(copy_file, dest_path)

    u.git_command(repo_path, ['add', dest_filename])

    status = u.git_command(repo_path, ['diff-index', '--quiet', 'HEAD', '--'])
    return status != 0


def create_groups_file(project, gerrit_api, repo_path):
    """
    :param project: 
    :param gerrit_api: 
    :param repo_path: 
    :type project: str 
    :type gerrit_api: GerritAPI
    :type repo_path: str
    :return: 
    """
    acl_config = os.path.join(repo_path, "project.config")
    group_file = os.path.join(repo_path, "groups")
    uuids = {}
    for line in open(acl_config, 'r'):
        r = re.match(r'^.*\sgroup\s+(.*)$', line)
        if r:
            group = r.group(1)
            if group in uuids.keys():
                continue
            uuid = gerrit_api.group_uuid(group)
            if uuid:
                uuids[group] = uuid
            else:
                log.error("Unable to get UUID for group %s." % group)
                raise CreateGroupException()
    if uuids:
        with open(group_file, 'w') as fp:
            for group, uuid in uuids.items():
                fp.write("%s\t%s\n" % (uuid, group))
        status = u.git_command(repo_path, ['add', 'groups'])
        if status != 0:
            log.error("Failed to add groups file for project: %s" % project)
            raise CreateGroupException()


def create_update_github_project(section, settings):
    """
    Create/Update the project on Github
    :param section: 
    :param settings: 
    :type section: JeepybProjectConfig
    :type settings: JeepybSettings
    :return: True if project was created on Github
    """
    created = False
    has_issues = 'has-issues' in section.options or settings.default_has_issues
    has_downloads = 'has-downloads' in section.options or settings.default_has_downloads
    has_wiki = 'has-wiki' in section.options or settings.default_has_wiki

    needs_update = False
    if not section.cache.get('created-in-github', False):
        needs_update = True
    if not section.cache.get('gerrit-in-team', False):
        needs_update = True
    if section.cache.get('has_issues', settings.default_has_issues) != has_issues:
        needs_update = True
    if section.cache.get('has_downloads', settings.default_has_downloads) != has_downloads:
        needs_update = True
    if section.cache.get('has_wiki', settings.default_has_wiki) != has_wiki:
        needs_update = True
    if not needs_update:
        return False

    secure_config = ConfigParser.ConfigParser()
    secure_config.read(settings.github_secure_config)

    global orgs
    if orgs is None:
        if secure_config.has_option("github", "oauth_token"):
            ghub = github.Github(secure_config.get("github", "oauth_token"))
        else:
            ghub = github.Github(secure_config.get("github", "username"),
                                 secure_config.get("github", "password"))

        log.info('Fetching github org list')
        orgs = ghub.get_user().get_orgs()
    orgs_dict = dict(zip([o.login.lower() for o in orgs], orgs))

    # Find the project's repo
    project_split = section.project_name.split('/', 1)
    org_name = project_split[0]
    if len(project_split) > 1:
        repo_name = project_split[1]
    else:
        repo_name = section.project_name

    try:
        org = orgs_dict[org_name.lower()]
    except KeyError:
        # We do not have control of this github org ignore the project.
        return False

    try:
        log.info("Fetching github info about %s", repo_name)
        repo = org.get_repo(repo_name)

    except github.GithubException:
        log.info("Creating %s in github", repo_name)
        repo = org.create_repo(repo_name,
                               homepage=section.homepage,
                               has_issues=has_issues,
                               has_downloads=has_downloads,
                               has_wiki=has_wiki)
        created = True

    section.cache['created-in-github'] = True
    section.cache['has_wiki'] = has_wiki
    section.cache['has_downloads'] = has_downloads
    section.cache['has_issues'] = has_issues

    kwargs = {}
    # If necessary, update project on Github
    if section.description and section.description != repo.description:
        kwargs['description'] = section.description
    if section.homepage and section.homepage != repo.homepage:
        kwargs['homepage'] = section.homepage
    if has_issues != repo.has_issues:
        kwargs['has_issues'] = has_issues
    if has_downloads != repo.has_downloads:
        kwargs['has_downloads'] = has_downloads
    if has_wiki != repo.has_wiki:
        kwargs['has_wiki'] = has_wiki

    if kwargs:
        log.info("Updating github repo info about %s", repo_name)
        repo.edit(repo_name, **kwargs)
    section.cache.update(kwargs)

    if not section.cache.get('gerrit-in-team', False):
        if 'gerrit' not in [team.name for team in repo.get_teams()]:
            log.info("Adding gerrit to github team for %s", repo_name)
            teams = org.get_teams()
            teams_dict = dict(zip([t.name.lower() for t in teams], teams))
            teams_dict['gerrit'].add_to_repos(repo)
        section.cache['gerrit-in-team'] = True
        created = True

    return created


# TODO(mordred): Inspect repo_dir:master for a description
#                override
def find_description_override(repo_path):
    return None


def process_acls(acl_path, section, gerrit_api):
    """
    Push Project ACLs to Gerrit
    :param acl_path: 
    :param section: 
    :param gerrit_api: 
    :type acl_path: str
    :type section: JeepybProjectConfig
    :type gerrit_api: GerritAPI
    :return: 
    """
    if not os.path.isfile(acl_path):
        log.warning('ACL Config was not found, %s' % acl_path)
        return

    # Use context manager to checkout and push any changes
    with gerrit_api.meta_updater(project=section.project_name, checkout_path=section.repo_path):
        dirty_repo = copy_file_to_git_repo(repo_path=section.repo_path,
                                           copy_file=acl_path,
                                           dest_filename='project.config')
        if not dirty_repo:
            # nothing was modified, so we're done
            return

        # Only create groups file if the ACL file was changed
        create_groups_file(project=section.project_name,
                           gerrit_api=gerrit_api,
                           repo_path=section.repo_path)


def create_groups(group_config, gerrit_api):
    if not os.path.isfile(group_config):
        return
    with open(group_config, 'r') as f:
        yaml_groups = yaml.safe_load(f)

    for group in yaml_groups:
        ldap_groups = group.get('ldap-groups', [])
        if len(ldap_groups):
            ldap_groups = ['ldap:' + g for g in ldap_groups]

        gerrit_api.create_group(name=group['name'],
                                description=group.get('description'),
                                members=group.get('members', []),
                                subgroups=group.get('subgroups', []),
                                ldap_groups=ldap_groups)


def process_prolog_rules(prolog_path, section, gerrit_api):
    """
    Push Project Prolog Rules to Gerrit
    :param prolog_path: 
    :param section: 
    :param gerrit_api: 
    :type prolog_path: str
    :type section: JeepybProjectConfig
    :type gerrit_api: GerritAPI
    :return: 
    """
    if not os.path.isfile(prolog_path):
        log.warning('Prolog rules file was not found, %s' % prolog_path)
        return

    # Use context manager to checkout and push any changes
    with gerrit_api.meta_updater(project=section.project_name, checkout_path=section.repo_path):
        copy_file_to_git_repo(repo_path=section.repo_path, copy_file=prolog_path, dest_filename='rules.pl')


def create_gerrit_project(section, gerrit_api):
    """
    Creates a project in Gerrit
    :param section: JeepybProjectConfig
    :param gerrit_api: GerritAPI
    :return: True if project was created, False otherwise
    """
    if section.project_name in gerrit_api.projects:
        log.info('Project (%s) already exists in Gerrit' % section.project_name)
        section.cache['project-created'] = True
        return False
    try:
        gerrit_api.create_project(name=section.project_name,
                                  description=section.description,
                                  is_parent=section.is_parent,
                                  parent_project=section.parent_project)
        section.cache['project-created'] = True
        return True
    except Exception:
        log.exception(
            "Exception creating %s in Gerrit." % section.project_name)
        section.cache['project-created'] = False
        raise


def generate_sha_for_dir(path, extension='.config'):
    sha_cache = {}
    for config_file in glob.glob(os.path.join(path, '*/*%s' % extension)):
        sha256 = hashlib.sha256()
        with open(config_file, 'r') as f:
            sha256.update(f.read())
        sha_cache[config_file] = sha256.hexdigest()

    return sha_cache


def main():
    parser = argparse.ArgumentParser(description='Manage projects')
    l.setup_logging_arguments(parser)
    parser.add_argument('--nocleanup', action='store_true',
                        help='do not remove temp directories')
    parser.add_argument('--project-config-dir', action='store',
                        default=None,
                        help='Location of the project-config repo')
    parser.add_argument('projects', metavar='project', nargs='*',
                        help='name of project(s) to process')
    args = parser.parse_args()
    l.configure_logging(args)

    # Generate Jeepyb Settings
    settings = JeepybSettings(args.project_config_dir)

    # Generate hashes of current configurations
    acl_cache = generate_sha_for_dir(settings.acl_dir, extension='.config')
    group_cache = generate_sha_for_dir(settings.group_dir, extension='.yaml')
    prolog_cache = generate_sha_for_dir(settings.prolog_dir, extension='.pl')

    gerrit_api = GerritAPI(host=settings.gerrit_host,
                           user=settings.gerrit_user,
                           port=settings.gerrit_port,
                           ssh_key=settings.gerrit_key,
                           url=settings.gerrit_url,
                           http_pass=settings.gerrit_http_pass,
                           gitid=settings.gerrit_gitid,
                           system_user=settings.gerrit_os_system_user,
                           system_group=settings.gerrit_os_system_group)

    with settings as configs_list:
        for config_section in configs_list:
            with config_section as section:
                # Skip the project is not defined in CLI argument
                if args.projects and section.project_name not in args.projects:
                    continue
                # If this project doesn't want to use gerrit, exit cleanly.
                if section.no_gerrit:
                    continue

                # Create the project if not already created
                if not section.already_created:
                    try:
                        create_gerrit_project(section, gerrit_api)
                        section.cache['project-created'] = True
                    except Exception:
                        section.cache['project-created'] = False
                        continue

                # Create a Checkout object to process repos locally
                checkout = GerritCheckout(project=section.project_name,
                                          checkout_path=section.repo_path,
                                          upstream=section.upstream,
                                          gerrit_api=gerrit_api)

                # Push to Gerrit if we have not already
                if not section.cache_pushed_to_gerrit:
                    # We haven't pushed to gerrit, so grab the repo again
                    u.remove_dir_if_exists(section.repo_path)

                    # Make Local repo
                    push_string = checkout.make_local_copy()

                    section.description = find_description_override(section.repo_path) or section.description

                    # Check repo health
                    checkout.fsck_repo()

                    # Push to Gerrit if local repo was created
                    if push_string:
                        checkout.push_to_gerrit(push_string)
                    section.cache['pushed-to-gerrit'] = True

                    # Replicate to other Git Repos (if enabled)
                    if settings.gerrit_replicate:
                        gerrit_api.replicate(section.project_name)

                # Create the repo for the local git mirror
                # todo: evaluate if this is even needed
                checkout.create_local_mirror(settings.local_git_dir)

                # Process ACL Configuration
                if section.acl_config:
                    acl_name = u.fixup_path(section.acl_config)
                    acl_sha = {k: v for k, v in acl_cache.items() if acl_name in k}
                    for name, sha in acl_sha.items():
                        if section.cache.get('acl-sha') != sha:
                            process_acls(name, section, gerrit_api)
                            section.cache['acl-sha'] = sha
                        else:
                            log.info("%s has matching sha, skipping ACLs",
                                     section.project_name)

                # Process Groups
                if section.groups:
                    group_name = u.fixup_path(section.groups)
                    group_sha = {k: v for k, v in group_cache.items() if group_name in k}
                    for name, sha in group_sha.items():
                        if section.cache.get('groups-sha') == sha:
                            log.info('No changes to %s groups file', section.project_name)
                            continue
                        create_groups(group_config=name, gerrit_api=gerrit_api)
                        section.cache['groups-sha'] = group_sha

                # Process Prolog Rules
                if section.prolog_rule:
                    prolog_path = u.fixup_path(section.prolog_rule)
                    prolog_sha = {k: v for k, v in prolog_cache.items() if prolog_path in k}
                    for name, sha in prolog_sha.items():
                        if section.cache.get('prolog-sha') == sha:
                            log.info("No changes to %s prolog rules", section.project_name)
                            continue
                        process_prolog_rules(name, section, gerrit_api)
                        section.cache['prolog-sha'] = sha

                # Push to Github
                if 'has-github' in section.options or settings.default_has_github:
                    created = create_update_github_project(section, settings)
                    if created and settings.gerrit_replicate:
                        gerrit_api.replicate(section.project_name)

if __name__ == "__main__":
    main()
