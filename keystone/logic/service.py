# Copyright (c) 2010-2011 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import datetime, timedelta
import uuid

from keystone.logic.types import auth, atom
import keystone.backends as backends
import keystone.backends.api as api
import keystone.backends.models as models
from keystone.logic.types import fault
from keystone.logic.types.tenant import \
    Tenant, Tenants, User as TenantUser
from keystone.logic.types.role import Role, RoleRef, RoleRefs, Roles
from keystone.logic.types.service import Service, Services
from keystone.logic.types.user import User, User_Update, Users
from keystone.logic.types.endpoint import Endpoint, Endpoints, \
    EndpointTemplate, EndpointTemplates
import keystone.utils as utils


class IdentityService(object):
    """Implements Identity service"""

    #
    #  Token Operations
    #
    def authenticate(self, credentials):
        # Check credentials
        if not isinstance(credentials, auth.PasswordCredentials):
            raise fault.BadRequestFault("Expecting Password Credentials!")

        if not credentials.tenant_id:
            duser = api.user.get(credentials.username)
            if duser == None:
                raise fault.UnauthorizedFault("Unauthorized")
        else:
            duser = api.user.get_by_tenant(credentials.username,
                credentials.tenant_id)
            if duser == None:
                raise fault.UnauthorizedFault("Unauthorized on this tenant")

        if not duser.enabled:
            raise fault.UserDisabledFault("Your account has been disabled")
        if duser.password != utils.get_hashed_password(credentials.password):
            raise fault.UnauthorizedFault("Unauthorized")
        
        #
        # Look for an existing token, or create one,
        # TODO: Handle tenant/token search
        #
        if not credentials.tenant_id:
            dtoken = api.token.get_for_user(duser.id)
        else:
            dtoken = api.token.get_for_user_by_tenant(duser.id,
                                                  credentials.tenant_id)
        
        tenant_id = credentials.tenant_id or duser.tenant_id
        
        if not dtoken or dtoken.expires < datetime.now():
            # Create new token
            dtoken = models.Token()
            dtoken.id = str(uuid.uuid4())
            dtoken.user_id = duser.id
            if credentials.tenant_id:
                dtoken.tenant_id = credentials.tenant_id
            dtoken.expires = datetime.now() + timedelta(days=1)
            api.token.create(dtoken)
        #if tenant_id is passed in the call that tenant_id is passed else
        #user's default tenant_id is used.
        return self.__get_auth_data(dtoken, tenant_id)

    def validate_token(self, admin_token, token_id, belongs_to=None):
        self.__validate_admin_token(admin_token)
        
        if not api.token.get(token_id):
            raise fault.UnauthorizedFault("Bad token, please reauthenticate")
        
        (token, user) = self.__validate_token(token_id, belongs_to)
        
        return self.__get_validate_data(token, user)

    def revoke_token(self, admin_token, token_id):
        self.__validate_admin_token(admin_token)

        dtoken = api.token.get(token_id)
        if not dtoken:
            raise fault.ItemNotFoundFault("Token not found")

        api.token.delete(token_id)

    #
    #   Tenant Operations
    #

    def create_tenant(self, admin_token, tenant):
        self.__validate_admin_token(admin_token)

        if not isinstance(tenant, Tenant):
            raise fault.BadRequestFault("Expecting a Tenant")

        if tenant.tenant_id == None:
            raise fault.BadRequestFault("Expecting a unique Tenant Id")

        if api.tenant.get(tenant.tenant_id) != None:
            raise fault.TenantConflictFault(
                "A tenant with that id already exists")

        dtenant = models.Tenant()
        dtenant.id = tenant.tenant_id
        dtenant.desc = tenant.description
        dtenant.enabled = tenant.enabled

        api.tenant.create(dtenant)
        return tenant

    ##
    ##    GET Tenants with Pagination
    ##
    def get_tenants(self, admin_token, marker, limit, url):
        try:
            (_token, user) = self.__validate_admin_token(admin_token)
            # If Global admin return all 
            ts = []
            dtenants = api.tenant.get_page(marker, limit)
            for dtenant in dtenants:
                ts.append(Tenant(dtenant.id,
                                         dtenant.desc, dtenant.enabled))
            prev, next = api.tenant.get_page_markers(marker, limit)
            links = []
            if prev:
                links.append(atom.Link('prev',
                    "%s?'marker=%s&limit=%s'" % (url, prev, limit)))
            if next:
                links.append(atom.Link('next',
                    "%s?'marker=%s&limit=%s'" % (url, next, limit)))
            return Tenants(ts, links)
        except fault.UnauthorizedFault:
            #If not global admin ,return tenants specific to user.
            (_token, user) = self.__validate_token(admin_token, False)
            ts = []
            dtenants = api.tenant.tenants_for_user_get_page(
                user, marker, limit)
            for dtenant in dtenants:
                ts.append(Tenant(dtenant.id,
                                         dtenant.desc, dtenant.enabled))
            prev, next = api.tenant.tenants_for_user_get_page_markers(
                user, marker, limit)
            links = []
            if prev:
                links.append(atom.Link('prev',
                    "%s?'marker=%s&limit=%s'" % (url, prev, limit)))
            if next:
                links.append(atom.Link('next',
                    "%s?'marker=%s&limit=%s'" % (url, next, limit)))
            return Tenants(ts, links)

    def get_tenant(self, admin_token, tenant_id):
        self.__validate_admin_token(admin_token)

        dtenant = api.tenant.get(tenant_id)
        if not dtenant:
            raise fault.ItemNotFoundFault("The tenant could not be found")
        return Tenant(dtenant.id, dtenant.desc, dtenant.enabled)

    def update_tenant(self, admin_token, tenant_id, tenant):
        self.__validate_admin_token(admin_token)

        if not isinstance(tenant, Tenant):
            raise fault.BadRequestFault("Expecting a Tenant")
        
        dtenant = api.tenant.get(tenant_id)
        if dtenant == None:
            raise fault.ItemNotFoundFault("The tenant could not be found")
        values = {'desc': tenant.description, 'enabled': tenant.enabled}
        api.tenant.update(tenant_id, values)
        return Tenant(dtenant.id, tenant.description, tenant.enabled)

    def delete_tenant(self, admin_token, tenant_id):
        self.__validate_admin_token(admin_token)
        
        dtenant = api.tenant.get(tenant_id)
        if dtenant == None:
            raise fault.ItemNotFoundFault("The tenant could not be found")
        
        if not api.tenant.is_empty(tenant_id):
            raise fault.ForbiddenFault("You may not delete a tenant that "
                                       "contains get_users")
        
        api.tenant.delete(dtenant.id)
        return None

    #
    # Private Operations
    #
    def __get_dauth_data(self, token_id):
        """return token and user object for a token_id"""

        token = None
        user = None
        if token_id:
            token = api.token.get(token_id)
            if token:
                user = api.user.get(token.user_id)
        return (token, user)

    #
    #   User Operations
    #
    def create_user(self, admin_token, user):
        self.__validate_admin_token(admin_token)

        dtenant = self.validate_and_fetch_user_tenant(user.tenant_id)

        if not isinstance(user, User):
            raise fault.BadRequestFault("Expecting a User")

        if user.user_id == None or len(user.user_id.strip()) == 0:
            raise fault.BadRequestFault("Expecting a unique User Id")

        if api.user.get(user.user_id) != None:
            raise fault.UserConflictFault(
                "An user with that id already exists")

        if api.user.get_by_email(user.email) != None:
            raise fault.EmailConflictFault("Email already exists")

        duser = models.User()
        duser.id = user.user_id
        duser.password = user.password
        duser.email = user.email
        duser.enabled = user.enabled
        duser.tenant_id = user.tenant_id
        api.user.create(duser)

        return user

    def validate_and_fetch_user_tenant(self, tenant_id):
        if tenant_id != None and len(tenant_id) > 0:
            dtenant = api.tenant.get(tenant_id)
            if dtenant == None:
                raise fault.ItemNotFoundFault("The tenant is not found")
            elif not dtenant.enabled:
                raise fault.TenantDisabledFault(
                    "Your account has been disabled")
            return dtenant
        else:
            return None

    def get_tenant_users(self, admin_token, tenant_id, marker, limit, url):
        self.__validate_admin_token(admin_token)

        if tenant_id == None:
            raise fault.BadRequestFault("Expecting a Tenant Id")
        dtenant = api.tenant.get(tenant_id)
        if dtenant is  None:
            raise fault.ItemNotFoundFault("The tenant not found")
        if not dtenant.enabled:
            raise fault.TenantDisabledFault("Your account has been disabled")
        ts = []
        dtenantusers = api.user.users_get_by_tenant_get_page(tenant_id, marker,
                                                          limit)
        for dtenantuser in dtenantusers:
            ts.append(User(None, dtenantuser.id, tenant_id,
                                   dtenantuser.email, dtenantuser.enabled))
        links = []
        if ts.__len__():
            prev, next = api.user.users_get_by_tenant_get_page_markers(
                    tenant_id, marker, limit)
            if prev:
                links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" % 
                                      (url, prev, limit)))
            if next:
                links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" % 
                                      (url, next, limit)))
        return Users(ts, links)

    def get_users(self, admin_token, marker, limit, url):
        self.__validate_admin_token(admin_token)
        ts = []
        dusers = api.user.users_get_page(marker, limit)
        for duser in dusers:
            ts.append(User(None, duser.id, duser.tenant_id,
                                   duser.email, duser.enabled))
        links = []
        if ts.__len__():
            prev, next = api.user.users_get_page_markers(marker, limit)
            if prev:
                links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" % 
                                      (url, prev, limit)))
            if next:
                links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" % 
                                      (url, next, limit)))
        return Users(ts, links)

    def get_user(self, admin_token, user_id):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)
        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        dtenant = api.tenant.get(duser.tenant_id)

        ts = []
        return User_Update(None, duser.id, duser.tenant_id,
                duser.email, duser.enabled)

    def update_user(self, admin_token, user_id, user):
        self.__validate_admin_token(admin_token)

        duser = api.user.get(user_id)

        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        if not isinstance(user, User):
            raise fault.BadRequestFault("Expecting a User")

        if user.email != duser.email and \
            api.user.get_by_email(user.email) is not None:
            raise fault.EmailConflictFault(
                "Email already exists")

        values = {'email': user.email}
        api.user.update(user_id, values)
        duser = api.user.user_get_update(user_id)
        return User(duser.password, duser.id, duser.tenant_id,
                          duser.email, duser.enabled)

    def set_user_password(self, admin_token, user_id, user):
        self.__validate_admin_token(admin_token)

        duser = api.user.get(user_id)
        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        if not isinstance(user, User):
            raise fault.BadRequestFault("Expecting a User")

        duser = api.user.get(user_id)
        if duser == None:
            raise fault.ItemNotFoundFault("The user could not be found")

        values = {'password': user.password}

        api.user.update(user_id, values)

        return User_Update(user.password,
            None, None, None, None)

    def enable_disable_user(self, admin_token, user_id, user):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)
        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")
        if not isinstance(user, User):
            raise fault.BadRequestFault("Expecting a User")

        duser = api.user.get(user_id)
        if duser == None:
            raise fault.ItemNotFoundFault("The user could not be found")

        values = {'enabled': user.enabled}

        api.user.update(user_id, values)

        return User_Update(None,
            None, None, None, user.enabled)

    def set_user_tenant(self, admin_token, user_id, user):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)
        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")
        if not isinstance(user, User):
            raise fault.BadRequestFault("Expecting a User")

        duser = api.user.get(user_id)
        if duser == None:
            raise fault.ItemNotFoundFault("The user could not be found")

        dtenant = self.validate_and_fetch_user_tenant(user.tenant_id)
        values = {'tenant_id': user.tenant_id}
        api.user.update(user_id, values)
        return User_Update(None,
            None, user.tenant_id, None, None)

    def delete_user(self, admin_token, user_id):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)
        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        dtenant = api.tenant.get(duser.tenant_id)
        if dtenant != None:
            api.user.delete_tenant_user(user_id, dtenant.id)
        else:
            api.user.delete(user_id)
        return None

    def __get_auth_data(self, dtoken, tenant_id):
        """return AuthData object for a token"""
        endpoints = None
        if tenant_id != None:
            endpoints = api.tenant.get_all_endpoints(tenant_id)
        token = auth.Token(dtoken.expires, dtoken.id, tenant_id)
        return auth.AuthData(token, endpoints)

    def __get_validate_data(self, dtoken, duser):
        """return ValidateData object for a token/user pair"""

        token = auth.Token(dtoken.expires, dtoken.id, dtoken.tenant_id)
        ts = []
        if dtoken.tenant_id:
            droleRefs = api.role.ref_get_all_tenant_roles(duser.id,
                                                             dtoken.tenant_id)
            for droleRef in droleRefs:
                ts.append(RoleRef(droleRef.id, droleRef.role_id,
                                         droleRef.tenant_id))
        droleRefs = api.role.ref_get_all_global_roles(duser.id)
        for droleRef in droleRefs:
            ts.append(RoleRef(droleRef.id, droleRef.role_id,
                                     droleRef.tenant_id))
        user = auth.User(duser.id, duser.tenant_id, RoleRefs(ts, []))
        return auth.ValidateData(token, user)

    def __validate_tenant(self, tenant_id):
        if not tenant_id:
            raise fault.UnauthorizedFault("Missing tenant")
        
        tenant = api.tenant.get(tenant_id)
        
        if not tenant.enabled:
            raise fault.TenantDisabledFault("Tenant %s has been disabled!"
                                          % tenant.id)

    def __validate_token(self, token_id, belongs_to=None):
        if not token_id:
            raise fault.UnauthorizedFault("Missing token")
        
        (token, user) = self.__get_dauth_data(token_id)

        if not token:
            raise fault.ItemNotFoundFault("Bad token, please reauthenticate")
        
        if token.expires < datetime.now():
            raise fault.ForbiddenFault("Token expired, please renew")
        
        if not user.enabled:
            raise fault.UserDisabledFault("User %s has been disabled!"
                                          % user.id)
        
        if user.tenant_id:
            self.__validate_tenant(user.tenant_id)
        
        if token.tenant_id:
            self.__validate_tenant(token.tenant_id)
        
        if belongs_to and token.tenant_id != belongs_to:
            raise fault.UnauthorizedFault("Unauthorized on this tenant")
        
        return (token, user)
    
    def __validate_admin_token(self, token_id):
        (token, user) = self.__validate_token(token_id)
        
        for roleRef in api.role.ref_get_all_global_roles(user.id):
            if roleRef.role_id == backends.KeyStoneAdminRole and \
                    roleRef.tenant_id is None:
                return (token, user)
        
        raise fault.UnauthorizedFault(
            "You are not authorized to make this call")

    def create_role(self, admin_token, role):
        self.__validate_admin_token(admin_token)

        if not isinstance(role, Role):
            raise fault.BadRequestFault("Expecting a Role")

        if role.role_id == None:
            raise fault.BadRequestFault("Expecting a Role Id")

        if api.role.get(role.role_id) != None:
            raise fault.RoleConflictFault(
                "A role with that id already exists")
        drole = models.Role()
        drole.id = role.role_id
        drole.desc = role.desc
        api.role.create(drole)
        return role

    def get_roles(self, admin_token, marker, limit, url):
        self.__validate_admin_token(admin_token)

        ts = []
        droles = api.role.get_page(marker, limit)
        for drole in droles:
            ts.append(Role(drole.id,
                                     drole.desc))
        prev, next = api.role.get_page_markers(marker, limit)
        links = []
        if prev:
            links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" \
                                                % (url, prev, limit)))
        if next:
            links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" \
                                                % (url, next, limit)))
        return Roles(ts, links)

    def get_role(self, admin_token, role_id):
        self.__validate_admin_token(admin_token)

        drole = api.role.get(role_id)
        if not drole:
            raise fault.ItemNotFoundFault("The role could not be found")
        return Role(drole.id, drole.desc)

    def create_role_ref(self, admin_token, user_id, roleRef):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)

        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        if not isinstance(roleRef, RoleRef):
            raise fault.BadRequestFault("Expecting a Role Ref")

        if roleRef.role_id == None:
            raise fault.BadRequestFault("Expecting a Role Id")

        drole = api.role.get(roleRef.role_id)
        if drole == None:
            raise fault.ItemNotFoundFault("The role not found")

        if roleRef.tenant_id != None:
            dtenant = api.tenant.get(roleRef.tenant_id)
            if dtenant == None:
                raise fault.ItemNotFoundFault("The tenant not found")

        drole_ref = models.UserRoleAssociation()
        drole_ref.user_id = duser.id
        drole_ref.role_id = drole.id
        if roleRef.tenant_id != None:
            drole_ref.tenant_id = dtenant.id
        user_role_ref = api.user.user_role_add(drole_ref)
        roleRef.role_ref_id = user_role_ref.id
        return roleRef

    def delete_role_ref(self, admin_token, role_ref_id):
        self.__validate_admin_token(admin_token)
        api.role.ref_delete(role_ref_id)
        return None

    def get_user_roles(self, admin_token, marker, limit, url, user_id):
        self.__validate_admin_token(admin_token)
        duser = api.user.get(user_id)

        if not duser:
            raise fault.ItemNotFoundFault("The user could not be found")

        ts = []
        droleRefs = api.role.ref_get_page(marker, limit, user_id)
        for droleRef in droleRefs:
            ts.append(RoleRef(droleRef.id, droleRef.role_id,
                                     droleRef.tenant_id))
        prev, next = api.role.ref_get_page_markers(user_id, marker, limit)
        links = []
        if prev:
            links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" \
                                                % (url, prev, limit)))
        if next:
            links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" \
                                                % (url, next, limit)))
        return RoleRefs(ts, links)

    def get_endpoint_templates(self, admin_token, marker, limit, url):
        self.__validate_admin_token(admin_token)

        ts = []
        dendpointTemplates = api.endpoint_template.get_page(marker, limit)
        for dendpointTemplate in dendpointTemplates:
            ts.append(EndpointTemplate(
                dendpointTemplate.id,
                dendpointTemplate.region,
                dendpointTemplate.service,
                dendpointTemplate.public_url,
                dendpointTemplate.admin_url,
                dendpointTemplate.internal_url,
                dendpointTemplate.enabled,
                dendpointTemplate.is_global))
        prev, next = api.endpoint_template.get_page_markers(marker, limit)
        links = []
        if prev:
            links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" \
                                                % (url, prev, limit)))
        if next:
            links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" \
                                                % (url, next, limit)))
        return EndpointTemplates(ts, links)

    def get_endpoint_template(self, admin_token, endpoint_template_id):
        self.__validate_admin_token(admin_token)

        dendpointTemplate = api.endpoint_template.get(endpoint_template_id)
        if not dendpointTemplate:
            raise fault.ItemNotFoundFault(
                "The endpoint template could not be found")
        return EndpointTemplate(
            dendpointTemplate.id,
            dendpointTemplate.region,
            dendpointTemplate.service,
            dendpointTemplate.public_url,
            dendpointTemplate.admin_url,
            dendpointTemplate.internal_url,
            dendpointTemplate.enabled,
            dendpointTemplate.is_global)

    def get_tenant_endpoints(self, admin_token, marker, limit, url, tenant_id):
        self.__validate_admin_token(admin_token)
        if tenant_id == None:
            raise fault.BadRequestFault("Expecting a Tenant Id")

        if api.tenant.get(tenant_id) == None:
            raise fault.ItemNotFoundFault("The tenant not found")

        ts = []

        dtenantEndpoints = \
            api.endpoint_template.\
                endpoint_get_by_tenant_get_page(
                    tenant_id, marker, limit)
        for dtenantEndpoint in dtenantEndpoints:
            ts.append(Endpoint(dtenantEndpoint.id,
                    url + '/endpointTemplates/' + \
                    str(dtenantEndpoint.endpoint_template_id)))
        links = []
        if ts.__len__():
            prev, next = \
                api.endpoint_template.endpoint_get_by_tenant_get_page_markers(
                    tenant_id, marker, limit)
            if prev:
                links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" % 
                                      (url, prev, limit)))
            if next:
                links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" % 
                                      (url, next, limit)))
        return Endpoints(ts, links)

    def create_endpoint_for_tenant(self, admin_token,
                                     tenant_id, endpoint_template, url):
        self.__validate_admin_token(admin_token)
        if tenant_id == None:
            raise fault.BadRequestFault("Expecting a Tenant Id")
        if api.tenant.get(tenant_id) == None:
            raise fault.ItemNotFoundFault("The tenant not found")

        dendpoint_template = api.endpoint_template.get(endpoint_template.id)
        if not dendpoint_template:
            raise fault.ItemNotFoundFault(
                "The endpoint template could not be found")
        dendpoint = models.Endpoints()
        dendpoint.tenant_id = tenant_id
        dendpoint.endpoint_template_id = endpoint_template.id
        dendpoint = api.endpoint_template.endpoint_add(dendpoint)
        dendpoint = Endpoint(dendpoint.id, url + 
            '/endpointTemplates/' + dendpoint.endpoint_template_id)
        return dendpoint

    def delete_endpoint(self, admin_token, endpoint_id):
        self.__validate_admin_token(admin_token)
        api.endpoint_template.endpoint_delete(endpoint_id)
        return None
    
    #Service Operations
    def create_service(self, admin_token, service):
        self.__validate_admin_token(admin_token)

        if not isinstance(service, Service):
            raise fault.BadRequestFault("Expecting a Service")

        if service.service_id == None:
            raise fault.BadRequestFault("Expecting a Service Id")

        if api.service.get(service.service_id) != None:
            raise fault.ServiceConflictFault(
                "A service with that id already exists")
        dservice = models.Service()
        dservice.id = service.service_id
        dservice.desc = service.desc
        api.service.create(dservice)
        return service

    def get_services(self, admin_token, marker, limit, url):
        self.__validate_admin_token(admin_token)

        ts = []
        dservices = api.service.get_page(marker, limit)
        for dservice in dservices:
            ts.append(Service(dservice.id,
                                     dservice.desc))
        prev, next = api.service.get_page_markers(marker, limit)
        links = []
        if prev:
            links.append(atom.Link('prev', "%s?'marker=%s&limit=%s'" \
                                                % (url, prev, limit)))
        if next:
            links.append(atom.Link('next', "%s?'marker=%s&limit=%s'" \
                                                % (url, next, limit)))
        return Services(ts, links)

    def get_service(self, admin_token, service_id):
        self.__validate_admin_token(admin_token)

        dservice = api.service.get(service_id)
        if not dservice:
            raise fault.ItemNotFoundFault("The service could not be found")
        return Service(dservice.id, dservice.desc)

    def delete_service(self, admin_token, service_id):
        self.__validate_admin_token(admin_token)
        dservice = api.service.get(service_id)
        if not dservice:
            raise fault.ItemNotFoundFault("The service could not be found")
        api.service.delete(service_id)
    
