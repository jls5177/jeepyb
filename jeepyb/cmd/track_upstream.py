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

# track_upstream.py reads a config file called projects.ini and syncs
# a remote repo with the matching Gerrit repo

import argparse
import logging
import os

from jeepyb.gerrit import GerritAPI, GerritCheckout
from jeepyb.jeepyb_settings import JeepybSettings
import jeepyb.log as l


log = logging.getLogger("track_upstream")


def main():
    parser = argparse.ArgumentParser(description='Track Upstream Projects')
    l.setup_logging_arguments(parser)
    parser.add_argument('--nocleanup', action='store_true', default=False,
                        help='do not remove temp directories')
    parser.add_argument('--project-config-dir', action='store',
                        default=None,
                        help='Location of the project-config repo')
    parser.add_argument('projects', metavar='project', nargs='*',
                        help='name of project(s) to process')
    args = parser.parse_args()
    l.configure_logging(args)

    # Generate Jeepyb Settings
    settings = JeepybSettings(args.project_config_dir, update_cache=False,
                              cleanup_tmp=False if args.nocleanup else True)

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
                # Skip this project if it is not marked to track upstream
                if not section.track_upstream:
                    continue
                # Skip if this project is not pushed to Gerrit yet
                if not section.cache_pushed_to_gerrit:
                    log.warning('Project %s has not been pushed to Gerrit yet, skipping.' % section.project_name)
                    continue

                # Do not remove the working directory
                section.cleanup_tmp = False

                # Create a Checkout object to process repos locally
                checkout = GerritCheckout(project=section.project_name,
                                          checkout_path=section.repo_path,
                                          upstream=section.upstream,
                                          gerrit_api=gerrit_api)

                # Make Local repo
                if not os.path.exists(section.repo_path):
                    checkout.make_local_copy()
                else:
                    checkout.update_local_copy(section.track_upstream)

                checkout.fsck_repo()
                checkout.sync_upstream(section.upstream_prefix)


if __name__ == "__main__":
    main()
