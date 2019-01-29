# localslackirc
# Copyright (C) 2018-2019 Salvo "LtWorf" Tomaselli
#
# localslackirc is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# author Salvo "LtWorf" Tomaselli <tiposchi@tiscali.it>


import json
from functools import lru_cache
from ssl import SSLWantReadError
from struct import Struct
from time import sleep, monotonic
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from websocket import create_connection, WebSocket
from websocket._exceptions import WebSocketConnectionClosedException
from typedload import load

from slack import Channel, File, FileShared, IM, Message, MessageEdit, Profile, SlackEvent, Topic, User
from slackclient.client import Team, Self, LoginInfo

CALL_TIMEOUT = 10


class ChannelType(Struct):
    CHANNEL = 'p'
    QUERY = 'd'
    PUBLIC_CHANNEL = 'c'

class Rocket:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token  = token
        self._call_id = 100
        self._internalevents = []  # type: List[Dict[str, Any]]
        self._channels = []  # type: List[Channel]
        self._users = {}  # type: Dict[str, User]

        self._connect()

    @property
    def login_info(self):
        #TODO
        return LoginInfo(
            team=Team(
                id='',
                name='',
                domain='',
            ),
            self=Self(
                id='',
                name='LtWorf',
            ),
        )

    def _update_channels(self) -> None:
        data = self._call('rooms/get', [], True)  # type: Optional[List[Dict[str, Any]]]
        if not data:
            raise Exception('No channel list was returned')
        self._channels.clear()

        for i in data:
            # Subscribe to it
            self._subscribe('stream-room-messages', [
                            i['_id'],
                            {
                                'useCollection': False,
                                'args':[]
                            }
                        ]
            )

            # If it's a real channel
            if i.get('t') == ChannelType.CHANNEL:
                self._channels.append(Channel(
                    id=i['_id'],
                    name_normalized=i['fname'],
                    purpose=Topic(i.get('topic', '')),
                    topic=Topic(i.get('topic', '')),
                ))
            elif i.get('t') == ChannelType.PUBLIC_CHANNEL:
                self._channels.append(Channel(
                    id=i['_id'],
                    name_normalized=i['name'],
                    purpose=Topic(i.get('topic', '')),
                    topic=Topic(i.get('topic', '')),
                ))

    def _send_json(self, data: Dict[str, Any]) -> None:
        """
        Sends something raw over the websocket (normally a dictionary
        """
        self._websocket.send(json.dumps(data).encode('utf8'))

    def _connect(self) -> None:
        self._websocket = create_connection(self.url)
        self._websocket.sock.setblocking(0)
        self._send_json(
            {
                'msg': 'connect',
                'version': '1',
                'support': ['1']
            }
        )
        self._call('login', [{"resume": self.token}], False)
        self._update_channels()

    def _subscribe(self, name: str, params: List[Any]) -> bool:
        self._call_id += 1
        self._send_json(
            {
                'id': str(self._call_id),
                'msg': 'sub',
                'name': name,
                'params': params,
            }
        )
        initial = monotonic()
        while initial + CALL_TIMEOUT > monotonic():
            r = self._read(subs_id=str(self._call_id))
            if r:
                if r.get('msg') == 'ready':
                    return True
                return False
            sleep(0.05)
        raise TimeoutError()

    def _call(self, method: str, params: List[Any], wait_return: bool) -> Optional[Any]:
        """
        Does a remote call.

        if wait_return is true, it will wait for the response and
        return it. Otherwise the response will be ignored.
        """
        self._call_id += 1
        data = {
            'msg':'method',
            'method': method,
            'params': params,
            'id': str(self._call_id),
        }
        self._send_json(data)

        if wait_return:
            initial = monotonic()
            while initial + CALL_TIMEOUT > monotonic():
                r = self._read(str(self._call_id))
                if r:
                    return r
                sleep(0.05)
            raise TimeoutError()
        else:
            return None


    def away(self, is_away: bool) -> None:
        raise NotImplemented()

    @lru_cache()
    def get_members(self, id_: str) -> Set[str]:
        data = self._call('getUsersOfRoom', [id_ ,False], True)
        try:
            for i in data['records']:
                if i['_id'] not in self._users:
                    self._users[i['_id']] = User(
                        id=i['_id'],
                        name=i['username'],
                        profile=Profile(real_name=i.get('name', 'noname')),
                    )
        except:
            print('Fucked up data: ', data)
            raise
        return {i['_id'] for i in data['records']}

    def channels(self) -> List[Channel]:
        return self._channels

    def get_channel(self, id_: str) -> Channel:
        for i in self._channels:
            if i.id == id_:
                return i
        raise KeyError()

    def get_channel_by_name(self, name: str) -> Channel:
        for i in self._channels:
            if i.name == name:
                return i
        raise KeyError()

    def get_ims(self) -> List[IM]:
        raise NotImplemented()

    def get_user_by_name(self, name: str) -> User:
        for i in self._users.values():
            if i.name == name:
                return i
        raise KeyError()

    def get_usernames(self) -> List[str]:
        names = set()
        for i in self._users.values():
            names.add(i.name)
        return list(names)

    def prefetch_users(self) -> None:
        pass

    def get_user(self, id_: str) -> User:
        return self._users[id_]

    def get_file(self, f: Union[FileShared, str]) -> File:
        raise NotImplemented()

    def send_file(self, channel_id: str, filename: str) -> None:
        raise NotImplemented()

    def send_message(self, channel_id: str, msg: str) -> None:
        self._call_id += 1
        self._call('sendMessage', [
            {
                '_id': str(self._call_id),
                'msg': msg,
                'rid': channel_id,
            }
        ], False)

    def send_message_to_user(self, user_id: str, msg: str):
        raise NotImplemented()

    @property
    def fileno(self) -> Optional[int]:
        return self._websocket.fileno()

    def _read(self, event_id: Optional[str] = None, subs_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            _, raw_data = self._websocket.recv_data()
            if raw_data == b'\x03\xe8Normal closure':
                print('Server triggered a disconnect. Reaconnecting')
                raise Exception('Trigger reconnect')
        except SSLWantReadError:
            return None
        except:
            self._connect()
            return None
        data = json.loads(raw_data)

        # Handle the stupid ping thing directly here
        if data == {'msg': 'ping'}:
            self._send_json({'msg': 'pong'})
            return None

        # Search for results of function calls
        if data is not None and (event_id is not None or subs_id is not None):
            if data.get('msg') == 'result' and data.get('id') == event_id:
                return data['result']
            elif data.get('subs') == [subs_id]:
                return data
            else:
                # Not the needed item, append it there so it will be returned by the iterator later
                self._internalevents.append(data)
                return None
        else:
            return data

    def events_iter(self): # -> Iterator[Optional[SlackEvent]]:
        while True:
            if self._internalevents:
                data = self._internalevents.pop()
            else:
                data = self._read()

            if not data:
                yield None
                continue

            r = None  # type: Optional[SlackEvent]
            print('Scanning ', data)
            if not isinstance(data, dict):
                continue

            if data.get('msg') == 'changed' and data.get('collection') == 'stream-room-messages': # New message
                try:
                    # If the sender is unknown, add it
                    if data['fields']['args'][0]['u']['_id'] not in self._users:
                        self._users[data['fields']['args'][0]['u']['_id']] = User(
                            id=data['fields']['args'][0]['u']['_id'],
                            name=data['fields']['args'][0]['u']['username'],
                            profile=Profile(real_name='noname'),
                        )
                    r = Message(
                        channel=data['fields']['args'][0]['rid'],
                        user=data['fields']['args'][0]['u']['_id'],
                        text=data['fields']['args'][0]['msg'],
                    )
                    if 'editedBy' in data['fields']['args'][0]:
                        r = MessageEdit(
                            previous=Message(channel=r.channel, user=r.user, text=''),
                            current=r
                        )
                except:
                    pass

            if r is None:
                print('Not handled: ', data)
            else:
                yield r
