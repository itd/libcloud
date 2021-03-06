# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Rackspace driver
"""
import os

import base64
import warnings

from xml.etree import ElementTree as ET
from xml.parsers.expat import ExpatError

from libcloud.pricing import get_pricing, get_size_price, PRICING_DATA
from libcloud.common.base import Response
from libcloud.common.types import MalformedResponseError
from libcloud.compute.types import NodeState, Provider
from libcloud.compute.base import NodeDriver, Node
from libcloud.compute.base import NodeSize, NodeImage, NodeLocation

from libcloud.common.rackspace import (
    AUTH_HOST_US, AUTH_HOST_UK, RackspaceBaseConnection)


NAMESPACE = 'http://docs.rackspacecloud.com/servers/api/v1.0'


class RackspaceResponse(Response):

    def success(self):
        i = int(self.status)
        return i >= 200 and i <= 299

    def parse_body(self):
        if not self.body:
            return None
        try:
            body = ET.XML(self.body)
        except:
            raise MalformedResponseError(
                "Failed to parse XML",
                body=self.body,
                driver=RackspaceNodeDriver)
        return body

    def parse_error(self):
        # TODO: fixup, Rackspace only uses response codes really!
        try:
            body = ET.XML(self.body)
        except:
            raise MalformedResponseError(
                "Failed to parse XML",
                body=self.body, driver=RackspaceNodeDriver)
        try:
            text = "; ".join([err.text or ''
                              for err in
                              body.getiterator()
                              if err.text])
        except ExpatError:
            text = self.body
        return '%s %s %s' % (self.status, self.error, text)


class RackspaceConnection(RackspaceBaseConnection):
    """
    Connection class for the Rackspace driver
    """

    responseCls = RackspaceResponse
    auth_host = AUTH_HOST_US
    _url_key = "server_url"

    def __init__(self, user_id, key, secure=True):
        super(RackspaceConnection, self).__init__(user_id, key, secure)
        self.api_version = 'v1.0'
        self.accept_format = 'application/xml'

    def request(self, action, params=None, data='', headers=None,
                method='GET'):
        if not headers:
            headers = {}
        if not params:
            params = {}
        # Due to first-run authentication request, we may not have a path
        if self.server_url:
            action = self.server_url + action
        if method in ("POST", "PUT"):
            headers = {'Content-Type': 'application/xml; charset=UTF-8'}
        if method == "GET":
            params['cache-busting'] = os.urandom(8).encode('hex')
        return super(RackspaceConnection, self).request(
            action=action,
            params=params, data=data,
            method=method, headers=headers
        )


class RackspaceSharedIpGroup(object):
    """
    Shared IP group info.
    """

    def __init__(self, id, name, servers=None):
        self.id = str(id)
        self.name = name
        self.servers = servers


class RackspaceNodeIpAddresses(object):
    """
    List of public and private IP addresses of a Node.
    """

    def __init__(self, public_addresses, private_addresses):
        self.public_addresses = public_addresses
        self.private_addresses = private_addresses


class RackspaceNodeDriver(NodeDriver):
    """
    Rackspace node driver.

    Extra node attributes:
        - password: root password, available after create.
        - hostId: represents the host your cloud server runs on
        - imageId: id of image
        - flavorId: id of flavor
    """
    connectionCls = RackspaceConnection
    type = Provider.RACKSPACE
    api_name = 'rackspace'
    name = 'Rackspace'

    _rackspace_prices = get_pricing(driver_type='compute',
                                    driver_name='rackspace')

    features = {"create_node": ["generates_password"]}

    NODE_STATE_MAP = {'BUILD': NodeState.PENDING,
                      'REBUILD': NodeState.PENDING,
                      'ACTIVE': NodeState.RUNNING,
                      'SUSPENDED': NodeState.TERMINATED,
                      'QUEUE_RESIZE': NodeState.PENDING,
                      'PREP_RESIZE': NodeState.PENDING,
                      'VERIFY_RESIZE': NodeState.RUNNING,
                      'PASSWORD': NodeState.PENDING,
                      'RESCUE': NodeState.PENDING,
                      'REBUILD': NodeState.PENDING,
                      'REBOOT': NodeState.REBOOTING,
                      'HARD_REBOOT': NodeState.REBOOTING,
                      'SHARE_IP': NodeState.PENDING,
                      'SHARE_IP_NO_CONFIG': NodeState.PENDING,
                      'DELETE_IP': NodeState.PENDING,
                      'UNKNOWN': NodeState.UNKNOWN}

    def list_nodes(self):
        return self._to_nodes(self.connection.request('/servers/detail')
                                             .object)

    def list_sizes(self, location=None):
        return self._to_sizes(self.connection.request('/flavors/detail')
                                             .object)

    def list_images(self, location=None):
        return self._to_images(self.connection.request('/images/detail')
                                              .object)

    def list_locations(self):
        """Lists available locations

        Locations cannot be set or retrieved via the API, but currently
        there are two locations, DFW and ORD.
        """
        return [NodeLocation(0, "Rackspace DFW1/ORD1", 'US', self)]

    def _change_password_or_name(self, node, name=None, password=None):
        uri = '/servers/%s' % (node.id)

        if not name:
            name = node.name

        body = {'xmlns': NAMESPACE,
                 'name': name}

        if password != None:
            body['adminPass'] = password

        server_elm = ET.Element('server', body)

        resp = self.connection.request(
            uri, method='PUT', data=ET.tostring(server_elm))

        if resp.status == 204 and password != None:
            node.extra['password'] = password

        return resp.status == 204

    def ex_set_password(self, node, password):
        """
        Sets the Node's root password.

        This will reboot the instance to complete the operation.

        L{node.extra['password']} will be set to the new value if the
        operation was successful.
        """
        return self._change_password_or_name(node, password=password)

    def ex_set_server_name(self, node, name):
        """
        Sets the Node's name.

        This will reboot the instance to complete the operation.
        """
        return self._change_password_or_name(node, name=name)

    def create_node(self, **kwargs):
        """Create a new rackspace node

        See L{NodeDriver.create_node} for more keyword args.
        @keyword    ex_metadata: Key/Value metadata to associate with a node
        @type       ex_metadata: C{dict}

        @keyword    ex_files:   File Path => File contents to create on
                                the node
        @type       ex_files:   C{dict}
        """
        name = kwargs['name']
        image = kwargs['image']
        size = kwargs['size']

        attributes = {'xmlns': NAMESPACE,
             'name': name,
             'imageId': str(image.id),
             'flavorId': str(size.id)
        }

        if 'ex_shared_ip_group' in kwargs:
            # Deprecate this. Be explicit and call the variable
            # ex_shared_ip_group_id since user needs to pass in the id, not the
            # name.
            warnings.warn('ex_shared_ip_group argument is deprecated. Please'
                          + ' use ex_shared_ip_group_id')

        if 'ex_shared_ip_group_id' in kwargs:
            shared_ip_group_id = kwargs['ex_shared_ip_group_id']
            attributes['sharedIpGroupId'] = shared_ip_group_id

        server_elm = ET.Element('server', attributes)

        metadata_elm = self._metadata_to_xml(kwargs.get("ex_metadata", {}))
        if metadata_elm:
            server_elm.append(metadata_elm)

        files_elm = self._files_to_xml(kwargs.get("ex_files", {}))
        if files_elm:
            server_elm.append(files_elm)
        resp = self.connection.request("/servers",
                                       method='POST',
                                       data=ET.tostring(server_elm))
        return self._to_node(resp.object)

    def ex_resize(self, node, size):
        """
        Change an existing server flavor / scale the server up or down.

        @keyword    node: node to resize.
        @param      node: C{Node}

        @keyword    size: new size.
        @param      size: C{NodeSize}
        """
        elm = ET.Element(
            'resize',
            {'xmlns': NAMESPACE,
             'flavorId': str(size.id),
            }
        )

        resp = self.connection.request("/servers/%s/action" % (node.id),
                                       method='POST',
                                       data=ET.tostring(elm))
        return resp.status == 202

    def ex_confirm_resize(self, node):
        """
        Confirm a resize request which is currently in progress. If a resize
        request is not explicitly confirmed or reverted it's automatically
        confirmed after 24 hours.

        For more info refer to the API documentation: http://goo.gl/zjFI1

        @keyword    node: node for which the resize request will be confirmed.
        @param      node: C{Node}
        """
        elm = ET.Element(
            'confirmResize',
            {'xmlns': NAMESPACE}
        )

        resp = self.connection.request("/servers/%s/action" % (node.id),
                                       method='POST',
                                       data=ET.tostring(elm))
        return resp.status == 204

    def ex_revert_resize(self, node):
        """
        Revert a resize request which is currently in progress.
        All resizes are automatically confirmed after 24 hours if they have
        not already been confirmed explicitly or reverted.

        For more info refer to the API documentation: http://goo.gl/AizBu

        @keyword    node: node for which the resize request will be reverted.
        @param      node: C{Node}
        """
        elm = ET.Element(
            'revertResize',
            {'xmlns': NAMESPACE}
        )

        resp = self.connection.request("/servers/%s/action" % (node.id),
                                       method='POST',
                                       data=ET.tostring(elm))
        return resp.status == 204

    def ex_rebuild(self, node_id, image_id):
        # @TODO: Remove those ifs in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        if isinstance(image_id, NodeImage):
            image_id = image_id.id

        elm = ET.Element(
            'rebuild',
            {'xmlns': NAMESPACE,
             'imageId': image_id,
            }
        )
        resp = self.connection.request("/servers/%s/action" % node_id,
                                       method='POST',
                                       data=ET.tostring(elm))
        return resp.status == 202

    def ex_create_ip_group(self, group_name, node_id=None):
        # @TODO: Remove this if in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        group_elm = ET.Element(
            'sharedIpGroup',
            {'xmlns': NAMESPACE,
             'name': group_name,
            }
        )

        if node_id:
            ET.SubElement(group_elm,
                'server',
                {'id': node_id}
            )

        resp = self.connection.request('/shared_ip_groups',
                                       method='POST',
                                       data=ET.tostring(group_elm))
        return self._to_shared_ip_group(resp.object)

    def ex_list_ip_groups(self, details=False):
        uri = '/shared_ip_groups/detail' if details else '/shared_ip_groups'
        resp = self.connection.request(uri,
                                       method='GET')
        groups = self._findall(resp.object, 'sharedIpGroup')
        return [self._to_shared_ip_group(el) for el in groups]

    def ex_delete_ip_group(self, group_id):
        uri = '/shared_ip_groups/%s' % group_id
        resp = self.connection.request(uri, method='DELETE')
        return resp.status == 204

    def ex_share_ip(self, group_id, node_id, ip, configure_node=True):
        # @TODO: Remove this if in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        if configure_node:
            str_configure = 'true'
        else:
            str_configure = 'false'

        elm = ET.Element(
            'shareIp',
            {'xmlns': NAMESPACE,
             'sharedIpGroupId': group_id,
             'configureServer': str_configure}
        )

        uri = '/servers/%s/ips/public/%s' % (node_id, ip)

        resp = self.connection.request(uri,
                                       method='PUT',
                                       data=ET.tostring(elm))
        return resp.status == 202

    def ex_unshare_ip(self, node_id, ip):
        # @TODO: Remove this if in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        uri = '/servers/%s/ips/public/%s' % (node_id, ip)

        resp = self.connection.request(uri,
                                       method='DELETE')
        return resp.status == 202

    def ex_list_ip_addresses(self, node_id):
        # @TODO: Remove this if in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        uri = '/servers/%s/ips' % node_id
        resp = self.connection.request(uri,
                                       method='GET')
        return self._to_ip_addresses(resp.object)

    def _metadata_to_xml(self, metadata):
        if len(metadata) == 0:
            return None

        metadata_elm = ET.Element('metadata')
        for k, v in metadata.items():
            meta_elm = ET.SubElement(metadata_elm, 'meta', {'key': str(k)})
            meta_elm.text = str(v)

        return metadata_elm

    def _files_to_xml(self, files):
        if len(files) == 0:
            return None

        personality_elm = ET.Element('personality')
        for k, v in files.items():
            file_elm = ET.SubElement(personality_elm,
                                     'file',
                                     {'path': str(k)})
            file_elm.text = base64.b64encode(v)

        return personality_elm

    def _reboot_node(self, node, reboot_type='SOFT'):
        resp = self._node_action(node, ['reboot', ('type', reboot_type)])
        return resp.status == 202

    def ex_soft_reboot_node(self, node):
        return self._reboot_node(node, reboot_type='SOFT')

    def ex_hard_reboot_node(self, node):
        return self._reboot_node(node, reboot_type='HARD')

    def reboot_node(self, node):
        return self._reboot_node(node, reboot_type='HARD')

    def destroy_node(self, node):
        uri = '/servers/%s' % (node.id)
        resp = self.connection.request(uri, method='DELETE')
        return resp.status == 202

    def ex_get_node_details(self, node_id):
        # @TODO: Remove this if in 0.6
        if isinstance(node_id, Node):
            node_id = node_id.id

        uri = '/servers/%s' % (node_id)
        resp = self.connection.request(uri, method='GET')
        if resp.status == 404:
            return None
        return self._to_node(resp.object)

    def _node_action(self, node, body):
        if isinstance(body, list):
            attr = ' '.join(['%s="%s"' % (item[0], item[1])
                             for item in body[1:]])
            body = '<%s xmlns="%s" %s/>' % (body[0], NAMESPACE, attr)
        uri = '/servers/%s/action' % (node.id)
        resp = self.connection.request(uri, method='POST', data=body)
        return resp

    def _to_nodes(self, object):
        node_elements = self._findall(object, 'server')
        return [self._to_node(el) for el in node_elements]

    def _fixxpath(self, xpath):
        # ElementTree wants namespaces in its xpaths, so here we add them.
        return "/".join(["{%s}%s" % (NAMESPACE, e) for e in xpath.split("/")])

    def _findall(self, element, xpath):
        return element.findall(self._fixxpath(xpath))

    def _to_node(self, el):
        def get_ips(el):
            return [ip.get('addr') for ip in el]

        def get_meta_dict(el):
            d = {}
            for meta in el:
                d[meta.get('key')] = meta.text
            return d

        public_ip = get_ips(self._findall(el,
                                          'addresses/public/ip'))
        private_ip = get_ips(self._findall(el,
                                          'addresses/private/ip'))
        metadata = get_meta_dict(self._findall(el, 'metadata/meta'))

        n = Node(id=el.get('id'),
                 name=el.get('name'),
                 state=self.NODE_STATE_MAP.get(
                     el.get('status'), NodeState.UNKNOWN),
                 public_ip=public_ip,
                 private_ip=private_ip,
                 driver=self.connection.driver,
                 extra={
                    'password': el.get('adminPass'),
                    'hostId': el.get('hostId'),
                    'imageId': el.get('imageId'),
                    'flavorId': el.get('flavorId'),
                    'uri': "https://%s%s/servers/%s" % (
                         self.connection.host,
                         self.connection.request_path, el.get('id')),
                    'metadata': metadata,
                 })
        return n

    def _to_sizes(self, object):
        elements = self._findall(object, 'flavor')
        return [self._to_size(el) for el in elements]

    def _to_size(self, el):
        s = NodeSize(id=el.get('id'),
                     name=el.get('name'),
                     ram=int(el.get('ram')),
                     disk=int(el.get('disk')),
                     bandwidth=None,  # XXX: needs hardcode
                     price=self._get_size_price(el.get('id')),  # Hardcoded,
                     driver=self.connection.driver)
        return s

    def _to_images(self, object):
        elements = self._findall(object, "image")
        return [self._to_image(el)
                for el in elements
                if el.get('status') == 'ACTIVE']

    def _to_image(self, el):
        i = NodeImage(id=el.get('id'),
                      name=el.get('name'),
                      driver=self.connection.driver,
                      extra={'updated': el.get('updated'),
                             'created': el.get('created'),
                             'status': el.get('status'),
                             'serverId': el.get('serverId'),
                             'progress': el.get('progress')})
        return i

    def ex_limits(self):
        """
        Extra call to get account's limits, such as
        rates (for example amount of POST requests per day)
        and absolute limits like total amount of available
        RAM to be used by servers.

        @return: C{dict} with keys 'rate' and 'absolute'
        """

        def _to_rate(el):
            rate = {}
            for item in el.items():
                rate[item[0]] = item[1]

            return rate

        def _to_absolute(el):
            return {el.get('name'): el.get('value')}

        limits = self.connection.request("/limits").object
        rate = [_to_rate(el) for el in self._findall(limits, 'rate/limit')]
        absolute = {}
        for item in self._findall(limits, 'absolute/limit'):
            absolute.update(_to_absolute(item))

        return {"rate": rate, "absolute": absolute}

    def ex_save_image(self, node, name):
        """Create an image for node.

        @keyword    node: node to use as a base for image
        @param      node: L{Node}
        @keyword    name: name for new image
        @param      name: C{string}
        """

        image_elm = ET.Element(
                'image',
                {'xmlns': NAMESPACE,
                    'name': name,
                    'serverId': node.id}
        )

        return self._to_image(self.connection.request("/images",
                    method="POST",
                    data=ET.tostring(image_elm)).object)

    def _to_shared_ip_group(self, el):
        servers_el = self._findall(el, 'servers')
        if servers_el:
            servers = [s.get('id')
                       for s in self._findall(servers_el[0], 'server')]
        else:
            servers = None
        return RackspaceSharedIpGroup(id=el.get('id'),
                                      name=el.get('name'),
                                      servers=servers)

    def _to_ip_addresses(self, el):
        return RackspaceNodeIpAddresses(
            [ip.get('addr') for ip in
             self._findall(self._findall(el, 'public')[0], 'ip')],
            [ip.get('addr') for ip in
             self._findall(self._findall(el, 'private')[0], 'ip')]
        )


class RackspaceUKConnection(RackspaceConnection):
    """
    Connection class for the Rackspace UK driver
    """
    auth_host = AUTH_HOST_UK


class RackspaceUKNodeDriver(RackspaceNodeDriver):
    """Driver for Rackspace in the UK (London)
    """

    name = 'Rackspace (UK)'
    connectionCls = RackspaceUKConnection

    def list_locations(self):
        return [NodeLocation(0, 'Rackspace UK London', 'UK', self)]


class OpenStackResponse(RackspaceResponse):

    def has_content_type(self, content_type):
        content_type_header = dict([(key, value) for key, value in
                                    self.headers.items()
                                    if key.lower() == 'content-type'])
        if not content_type_header:
            return False

        content_type_value = content_type_header['content-type'].lower()

        return content_type_value.find(content_type.lower()) > -1

    def parse_body(self):
        if not self.has_content_type('application/xml') or not self.body:
            return self.body

        try:
            return ET.XML(self.body)
        except:
            raise MalformedResponseError(
                'Failed to parse XML',
                body=self.body,
                driver=RackspaceNodeDriver)


class OpenStackConnection(RackspaceConnection):

    responseCls = OpenStackResponse

    def __init__(self, user_id, key, secure, host, port):
        super(OpenStackConnection, self).__init__(user_id, key, secure=secure)
        self.auth_host = host
        self.port = (port, port)


class OpenStackNodeDriver(RackspaceNodeDriver):
    name = 'OpenStack'
    connectionCls = OpenStackConnection

    def _get_size_price(self, size_id):
        if 'openstack' not in PRICING_DATA['compute']:
            return 0.0

        return get_size_price(driver_type='compute',
                              driver_name='openstack',
                              size_id=size_id)
