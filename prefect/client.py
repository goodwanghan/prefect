import os

import requests

import prefect
import ujson
from prefect.utilities.graphql import format_graphql_result


class AuthorizationError(Exception):
    pass


class Client:
    """
    Client for the Prefect API.
    """

    def __init__(self, server=None, token=None):
        if server is None:
            server = prefect.config.get('prefect', 'server')
        self._server = server
        self._token = token

        self.projects = Projects(client=self)
        self.flows = Flows(client=self)
        self.flow_runs = FlowRuns(client=self)
        self.task_runs = TaskRuns(client=self)

    # -------------------------------------------------------------------------
    # Utilities

    def _get(self, path, *, _json=True, **params):
        """
        Convenience function for calling the Prefect API with token auth.

        Args:
            path: the path of the API url. For example, to GET
                http://prefect-server/v1/auth/login, path would be 'login'.
            params: GET parameters
        """
        response = self._request(method='GET', path=path, params=params)
        if _json:
            response = response.json()
        return response

    def _post(self, path, *, _json=True, **params):
        """
        Convenience function for calling the Prefect API with token auth.

        Args:
            path: the path of the API url. For example, to POST
                http://prefect-server/v1/login, path would be 'login'.
            params: POST params
        """
        response = self._request(method='POST', path=path, params=params)
        if _json:
            response = response.json()
        return response

    def _delete(self, path, *, _json=True):
        """
        Convenience function for calling the Prefect API with token auth.

        Args:
            path: the path of the API url
        """
        response = self._request(method='delete', path=path)
        if _json:
            response = response.json()
        return response

    def graphql(self, query, **variables):
        """
        Convenience function for running queries against the Prefect GraphQL
        API
        """
        result = self._post(
            path='/graphql', query=query, variables=ujson.dumps(variables))
        if 'errors' in result:
            raise ValueError(result['errors'])
        else:
            return format_graphql_result(result).data

    def _request(self, method, path, params):
        path = path.lstrip('/').rstrip('/')

        def request_fn():
            if self._token is None:
                raise ValueError(
                    'Call Client.login() to set the client token.')
            url = os.path.join(self._server, path)
            headers = {'Authorization': 'Bearer ' + self._token}
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params)
            elif method == 'POST':
                response = requests.post(url, headers=headers, data=params)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError('Invalid method: {}'.format(method))

            response.raise_for_status()

            return response

        try:
            return request_fn()
        except requests.HTTPError as err:
            if err.response.status_code == 401:
                self.refresh_token()
                return request_fn()
            raise

    # -------------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------------

    def login(self, email, password):
        url = os.path.join(self._server, 'auth/login')
        response = requests.post(url, auth=(email, password))
        if not response.ok:
            raise ValueError('Could not log in.')
        self._token = response.json().get('token')

    def refresh_token(self):
        response = self._post(path='auth/refresh', token=self._token)
        self._token = response.json().get('token')


class ClientModule:

    path = ''

    def __init__(self, client, name=None):
        if name is None:
            name = type(self).__name__
        self._name = name
        self._client = client

    def __repr__(self):
        return '<Client Module: {name}>'.format(name=self._name)

    def _get(self, path, **params):
        path = path.lstrip('/')
        return self._client._get(os.path.join(self.path, path), **params)

    def _post(self, path, **data):
        path = path.lstrip('/')
        return self._client._post(os.path.join(self.path, path), **data)

    def _delete(self, path, **params):
        path = path.lstrip('/')
        return self._client._delete(os.path.join(self.path, path), **params)

    def _graphql(self, query, **variables):
        return self._client._graphql(query=query, **variables)


# -------------------------------------------------------------------------
# Projects


class Projects(ClientModule):

    path = '/projects'

    def create(self, name):
        return self.client._post(path='/', name=name)

    def list(self, per_page=100, page=1):
        """
        Lists all available projects
        """
        data = self.client.graphql(
            '''
             query ($perPage: Int!, $page: Int!){
                projects(perPage: $perPage, page: $page){
                    id
                    name
                }
             }
             ''',
            perPage=per_page,
            page=page)
        return data['projects']

    def flows(self, project_id):
        """
        Returns the Flows for the specified project
        """

        data = self.client.graphql(
            '''
             query ($projectId: Int!){
                projects(filter: {id: $projectId}) {
                    id
                    name
                    flows {
                        id
                        name
                    }
                }
             }
             ''',
            projectId=project_id)
        return data['projects']


# -------------------------------------------------------------------------
# Flows


class Flows(ClientModule):

    path = '/flows'

    def load(self, flow_id, safe=True):
        """
        Retrieve information about a Flow.

        Args:
            flow_id (int): the Flow's id
        """
        data = self._graphql(
            '''
            query($flowId: Int!) {
                flow(id: $flowId) {
                    serialized
                }
            }
            ''',
            flowId=flow_id)
        if safe:
            return prefect.Flow.safe_deserialize(data['flow']['serialized'])
        else:
            return prefect.Flow.deserialize(data['flow']['serialized'])

    def set_state(self, flow_id, state):
        """
        Update a Flow's state
        """
        return self._post(path='/{id}/state'.format(id=flow_id), state=state)

    def create(self, flow):
        """
        Submit a Flow to the server.
        """
        return self._post(
            path='/', serialized_flow=ujson.dumps(flow.serialize()))


# -------------------------------------------------------------------------
# FlowRuns


class FlowRuns(ClientModule):

    path = '/flowruns'

    def get_state(self, flow_run_id):
        """
        Retrieve a flow run's state
        """
        return self._get(path='/{id}/state'.format(id=flow_run_id))

    def set_state(self, flow_run_id, state, expected_state=None):
        """
        Retrieve a flow run's state
        """
        return self._post(
            path='/{id}/state'.format(id=flow_run_id),
            state=state,
            expected_state=expected_state)

    def create(self, flow_id, parameters=None, parent_taskrun_id=None):
        return self._post(
            path='flowruns',
            flow_id=flow_id,
            parameters=parameters,
            parent_taskrun_id=parent_taskrun_id)

    def run(self, flow_run_id, start_tasks=None, context=None):
        """
        Queue a flow run to be run
        """
        return self._post(
            path='/{id}'.format(id=flow_run_id),
            start_tasks=start_tasks,
            context=context)


# -------------------------------------------------------------------------
# TaskRuns


class TaskRuns(ClientModule):

    path = '/taskruns'

    def set_state(self, task_run_id, state, expected_state=None):
        """
        Retrieve a task run's state
        """
        return self._post(
            path='/{id}/state'.format(id=task_run_id),
            state=state,
            expected_state=expected_state)
