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

import json
import logging
import requests
import urllib
import yaml

from pygerrit2.rest import GerritRestAPI

log = logging.getLogger("jeepyb.gerritrestapi")


class GerritRestError(Exception):
    """Exception class for errors communicating with a gerrit server."""
    def __init__(self, http_status, *args, **kwargs):
        super(GerritRestError, self).__init__(*args, **kwargs)
        self.http_status = http_status
        self.message = '(%d) %s' % (self.http_status, self.message)


def convert_unicode_json_to_ascii(json_object):
    return yaml.safe_load(json.dumps(json_object))


class GerritRestApi(object):
    def __init__(self, host, username, password):
        self.host = host
        self.auth = requests.auth.HTTPDigestAuth(username, password)

    def read_json_response(self, url, host=None, reqtype='GET', body=None, expect_status=200, ignore_404=True, return_unicode=False, is_json=True):
        """
        Sends a request to the host and returns the JSON parsed response
        """
        rest = GerritRestAPI(url=host or self.host, auth=self.auth)

        keywords = dict()
        if is_json and body:
            body = json.dumps(body) if not isinstance(body, str) else body
            keywords = {'json': json.loads(body)}
        elif body:
            keywords = {'data': str(body)}

        try:
            if reqtype == 'GET':
                response_body, response = rest.get(url, return_response=True, **keywords)
            elif reqtype == 'PUT':
                response_body, response = rest.put(url, return_response=True, **keywords)
            elif reqtype == 'POST':
                if is_json:
                    response_body, response = rest.post(url, return_response=True, **keywords)
                else:
                    response_body, response = rest.post(url, return_response=True, **keywords)
            elif reqtype == 'DELETE':
                response_body, response = rest.delete(url, return_response=True)
            else:
                raise RuntimeError('Unsupported Request type: %s' % reqtype)
        except requests.HTTPError as e:
            if ignore_404 and e.response.status_code == 404:
                return None
            if e.response.status_code == expect_status:
                return None
            log.warn('%s' % e.response.content)
            log.warn('%s' % str(e))
            raise GerritRestError(e.response.status_code, '%s' % e.response.content)

        if response.status_code != expect_status:
            raise GerritRestError(response.status_code, 'Status code (%s) does not match expected code (%s)' %
                              (response.status_code, expect_status))

        if return_unicode:
            return response_body
        return convert_unicode_json_to_ascii(response_body)

    def add_group_members(self, group, members):
        """
        Adds a list of members to a group
        Documentation:
        https://gerrit-review.googlesource.com/Documentation/rest-api-groups.html#_add_group_members
        
        :param group: name of a group to add members to.
        :param members: iterable with emails of accounts to add to the group.
        :return: None if success, True if errors were detected
        :raises: UnexpectedResponseException: if call failed.
        """
        errors = None
        path = 'groups/%s/members.add' % group.rstrip('/')
        body = {'members': list(members)}
        try:
            _ = self.read_json_response(path, reqtype='POST', body=body, ignore_404=False)
        except GerritRestError as e:
            if e.http_status == 422:  # "Unprocessable Entity"
                log.warn('Failed to add the following members to group %s:\n%s' % (group, list(members)))
                errors = True
            pass

        return errors

    def add_internal_include_groups(self, group, include_groups):
        """
        Adds a list of internal groups as members of another group
        :param group: Parent group to add the groups to
        :param include_groups: list of groups to add
        :return: None if success, True if errors were detected
        """
        errors = None
        path = 'groups/%s/groups' % group.rstrip('/')
        body = {'groups': list(include_groups)}
        try:
            _ = self.read_json_response(path, reqtype='POST', body=body, ignore_404=False)
        except GerritRestError as e:
            if e.http_status == 422:  # "Unprocessable Entity"
                print('Failed to add the following include groups to group %s:\n%s' % (group, list(include_groups)))
                print('Error response: %s' % e.message)
                errors = True
            pass

        return errors

    def add_include_groups(self, group, include_groups):
        """
        Adds a list of internal/external groups as members of another group
        :param group: Parent group to add the groups to
        :param include_groups: list of groups to add
        :return: None if success, True if errors were detected
        """
        errors = None
        for current_group in include_groups:
            path = 'groups/%s/groups/%s' % (group.rstrip('/'), current_group)
            try:
                _ = self.read_json_response(path, reqtype='PUT', ignore_404=False, expect_status=201)
            except GerritRestError as e:
                if e.http_status == 422:  # "Unprocessable Entity"
                    print('Failed to add the following include groups to group %s:\n%s' % (group, list(include_groups)), e.message)
                    print('Error response: %s' % e.message)
                    errors = True
                elif e.http_status == 200:
                    log.info('Include Group is already present in group %s' % group)
                else:
                    raise

        return errors

    def get_group_details(self, group):
        path = 'groups/%s/detail' % group.rstrip('/')
        group_details = self.read_json_response(path)
        for inner_group in group_details['includes']:
            inner_group['id'] = urllib.unquote(inner_group['id'])
        return group_details

    def is_an_include_group(self, group, include_group):
        group_details = self.get_group_details(group)
        found = [grp for grp in group_details['includes'] if include_group == grp['id'] or include_group == grp['name']]
        return any(found)

