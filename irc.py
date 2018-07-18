#!/usr/bin/env python3
# localslackirc
# Copyright (C) 2018 Salvo "LtWorf" Tomaselli
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

import datetime
from enum import Enum
import re
import select
import socket
import argparse
from typing import *
from os.path import expanduser

import slack


# How slack expresses mentioning users
_MENTIONS_REGEXP = re.compile(r'<@([0-9A-Za-z]+)>')
_CHANNEL_MENTIONS_REGEXP = re.compile(r'<#[A-Z0-9]+\|([A-Z0-9\-a-z]+)>')


_SUBSTITUTIONS = [
    ('&amp;', '&'),
    ('&gt;', '>'),
    ('&lt;', '<'),
]


class Replies(Enum):
    RPL_UNAWAY = 305
    RPL_NOWAWAY = 306


#: Inactivity days to hide a MPIM
MPIM_HIDE_DELAY = datetime.timedelta(days=50)


class Client:
    def __init__(self, s, sl_client, *, nouserlist=False, autojoin=False):
        self.nick = b''
        self.username = b''
        self.realname = b''
        self.parted_channels = set()  # type: Set[bytes]

        self.s = s
        self.sl_client = sl_client

        self.nouserlist = nouserlist
        self.autojoin = autojoin

    def _nickhandler(self, cmd: bytes) -> None:
        _, nick = cmd.split(b' ', 1)
        self.nick = nick.strip()

    def _sendreply(self, code: Union[int,Replies], message: Union[str,bytes], extratokens: List[Union[str,bytes]] = []) -> None:
        codeint = code if isinstance(code, int) else code.value
        bytemsg = message if isinstance(message, bytes) else message.encode('utf8')

        extratokens.insert(0, self.nick)

        self.s.send(b':serenity %03d %s :%s\n' % (
            codeint,
            b' '.join(i if isinstance(i, bytes) else i.encode('utf8') for i in extratokens),
            bytemsg,
        ))


    def _userhandler(self, cmd: bytes) -> None:
        #TODO USER salvo 8 * :Salvatore Tomaselli
        self._sendreply(1, 'Hi, welcome to IRC')
        self._sendreply(2, 'Your host is serenity, running version miniircd-1.2.1')
        self._sendreply(3, 'This server was created sometime')
        self._sendreply(4, 'serenity miniircd-1.2.1 o o')
        self._sendreply(251, 'There are 1 users and 0 services on 1 server')

        if self.autojoin and not self.nouserlist:
            # We're about to load many users for each chan; instead of requesting each
            # profile on its own, batch load the full directory.
            self.sl_client.prefetch_users()

        if self.autojoin:

            mpim_cutoff = datetime.datetime.utcnow() - MPIM_HIDE_DELAY

            for sl_chan in self.sl_client.channels():
                if not sl_chan.is_member:
                    continue

                if sl_chan.is_mpim and (sl_chan.latest is None or sl_chan.latest.timestamp < mpim_cutoff):
                    continue

                channel_name = '#%s' % sl_chan.name_normalized
                self._send_chan_info(channel_name.encode('utf-8'), sl_chan)

    def _pinghandler(self, cmd: bytes) -> None:
        _, lbl = cmd.split(b' ', 1)
        self.s.send(b':serenity PONG serenity %s\n' % lbl)

    def _joinhandler(self, cmd: bytes) -> None:
        _, channel_name = cmd.split(b' ', 1)

        if channel_name in self.parted_channels:
            self.parted_channels.remove(channel_name)

        try:
            slchan = self.sl_client.get_channel_by_name(channel_name[1:].decode())
        except:
            return

        self._send_chan_info(channel_name, slchan)

    def _send_chan_info(self, channel_name: bytes, slchan: slack.Channel):
        if not self.nouserlist:
            userlist = []  # type List[bytes]
            for i in self.sl_client.get_members(slchan.id):
                try:
                    u = self.sl_client.get_user(i)
                except:
                    continue
                if u.deleted:
                    # Disabled user, skip it
                    continue
                name = u.name.encode('utf8')
                prefix = b'@' if u.is_admin else b''
                userlist.append(prefix + name)

            users = b' '.join(userlist)

        self.s.send(b':%s!salvo@127.0.0.1 JOIN %s\n' % (self.nick, channel_name))
        self._sendreply(332, slchan.real_topic, [channel_name])
        self._sendreply(353, b'' if self.nouserlist else users, ['=', channel_name])
        self._sendreply(366, 'End of NAMES list', [channel_name])

    def _privmsghandler(self, cmd: bytes) -> None:
        _, dest, msg = cmd.split(b' ', 2)
        if msg.startswith(b':'):
            msg = msg[1:]
        message = self._addmagic(msg.decode('utf8'))

        if dest.startswith(b'#'):
            self.sl_client.send_message(
                self.sl_client.get_channel_by_name(dest[1:].decode()).id,
                message
            )
        else:
            try:
                self.sl_client.send_message_to_user(
                    self.sl_client.get_user_by_name(dest.decode()).id,
                    message
                )
            except:
                print('Impossible to find user ', dest)

    def _listhandler(self, cmd: bytes) -> None:
        for c in self.sl_client.channels():
            self._sendreply(322, c.real_topic, [b'#' + c.name, str(c.num_members)])
        self._sendreply(323, 'End of LIST')

    def _modehandler(self, cmd: bytes) -> None:
        params = cmd.split(b' ', 2)
        self._sendreply(324, '', [params[1], '+'])

    def _parthandler(self, cmd: bytes) -> None:
        _, name = cmd.split(b' ', 1)
        self.parted_channels.add(name)

    def _awayhandler(self, cmd: bytes) -> None:
        is_away = b' ' in cmd
        self.sl_client.away(is_away)
        response = Replies.RPL_NOWAWAY if is_away else Replies.RPL_UNAWAY
        self._sendreply(response, 'Away status changed')

    def _whohandler(self, cmd: bytes) -> None:
        _, name = cmd.split(b' ', 1)
        if not name.startswith(b'#'):
            print('WHO not supported on ', name)
            return
        channel = self.sl_client.get_channel_by_name(name.decode()[1:])

        for i in self.sl_client.get_members(channel.id):
            user = self.sl_client.get_user(i)
            self._sendreply(352, '0 %s' % user.real_name, [name, user.name, '127.0.0.1 serenity', user.name, 'H'])
        self._sendreply(315, 'End of WHO list', [name])

    def sendmsg(self, from_: bytes, to: bytes, message: bytes) -> None:
        self.s.send(b':%s!salvo@127.0.0.1 PRIVMSG %s :%s\n' % (
            from_,
            to, #private message, or a channel
            message,
        ))

    def _addmagic(self, msg: str) -> str:
        """
        Adds magic codes and various things to
        outgoing messages
        """
        for i in _SUBSTITUTIONS:
            msg = msg.replace(i[1], i[0])
        msg = msg.replace('@here', '<!here>')
        msg = msg.replace('@channel', '<!channel>')
        msg = msg.replace('@yell', '<!channel>')
        msg = msg.replace('@shout', '<!channel>')
        msg = msg.replace('@attention', '<!channel>')

        # Extremely inefficient code to generate mentions
        # Just doing them client-side on the receiving end is too mainstream
        for username in self.sl_client.get_usernames():
            m = re.search(r'\b%s\b' % username, msg)
            if m:
                msg = msg[0:m.start()] + '<@%s>' % self.sl_client.get_user_by_name(username).id + msg[m.end():]
        return msg

    def parse_message(self, msg: str) -> Iterator[bytes]:
        for i in msg.split('\n'):
            if not i:
                continue

            # Replace all mentions with @user
            while True:
                mention = _MENTIONS_REGEXP.search(i)
                if not mention:
                    break
                i = (
                    i[0:mention.span()[0]] +
                    self.sl_client.get_user(mention.groups()[0]).name +
                    i[mention.span()[1]:]
                )

            # Replace all channel mentions
            while True:
                mention = _CHANNEL_MENTIONS_REGEXP.search(i)
                if not mention:
                    break
                i = (
                    i[0:mention.span()[0]] +
                    '#' +
                    mention.groups()[0] +
                    i[mention.span()[1]:]
                )

            for s in _SUBSTITUTIONS:
                i = i.replace(s[0], s[1])

            encoded = i.encode('utf8')

            encoded = encoded.replace(b'<!here>', b'yelling [%s]' % self.nick)
            encoded = encoded.replace(b'<!channel>', b'YELLING LOUDER [%s]' % self.nick)

            yield encoded


    def _message(self, sl_ev: Union[slack.Message, slack.MessageFileShare, slack.MessageDelete, slack.MessageBot], prefix: str=''):
        """
        Sends a message to the irc client
        """
        if hasattr(sl_ev, 'user'):
            source = self.sl_client.get_user(sl_ev.user).name.encode('utf8')  # type: ignore
            if source == self.nick:
                return
        else:
            source = b'bot'
        try:
            dest = b'#' + self.sl_client.get_channel(sl_ev.channel).name.encode('utf8')
        except KeyError:
            dest = source
        except Exception as e:
            print('Error: ', str(e))
            return
        if dest in self.parted_channels:
            # Ignoring messages, channel was left on IRC
            return
        for msg in self.parse_message(prefix + sl_ev.text):
            self.sendmsg(
                source,
                dest,
                msg
            )

    def slack_event(self, sl_ev):
        #TODO handle p2p messages
        if isinstance(sl_ev, slack.MessageDelete):
            self._message(sl_ev, '[deleted]')
        elif isinstance(sl_ev, slack.Message):
            self._message(sl_ev)
        elif isinstance(sl_ev, slack.MessageFileShare):
            prefix ='[File upload] %s %d %s\n' % (
                        sl_ev.file.mimetype,
                        sl_ev.file.size,
                        sl_ev.file.url_private,
                    )
            self._message(sl_ev, prefix)
        elif isinstance(sl_ev, slack.MessageEdit):
            if sl_ev.is_changed:
                self._message(sl_ev.diffmsg)
        elif isinstance(sl_ev, slack.MessageBot):
            self._message(sl_ev, '[%s]' % sl_ev.username)


    def command(self, cmd: bytes) -> None:
        if b' ' in cmd:
            cmdid, _ = cmd.split(b' ', 1)
        else:
            cmdid = cmd

        handlers = {
            b'NICK': self._nickhandler,
            b'USER': self._userhandler,
            b'PING': self._pinghandler,
            b'JOIN': self._joinhandler,
            b'PRIVMSG': self._privmsghandler,
            b'LIST': self._listhandler,
            b'WHO': self._whohandler,
            b'MODE': self._modehandler,
            b'PART': self._parthandler,
            b'AWAY': self._awayhandler,
            #QUIT
            #CAP LS
            #USERHOST
            #Unknown command:  b'TOPIC #cama :titolo del canale'
            #Unknown command:  b'whois TAMARRO'
            #Unknown command:  b'PART #support-sdp :Konversation terminated!'
        }

        if cmdid in handlers:
            handlers[cmdid](cmd)
        else:
            self._sendreply(421, 'Unknown command', [cmdid])
            print('Unknown command: ', cmd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port', type=int, action='store', dest='port',
                                default=9007, required=False,
                                help='set port number')
    parser.add_argument('-i', '--ip', type=str, action='store', dest='ip',
                                default='127.0.0.1', required=False,
                                help='set ip address')
    parser.add_argument('-t', '--tokenfile', type=str, action='store', dest='tokenfile',
                                default=expanduser('~')+'/.localslackirc',
                                required=False,
                                help='set the token file')
    parser.add_argument('-u', '--nouserlist', action='store_true',
                                dest='nouserlist', required=False,
                                help='don\'t display userlist')
    parser.add_argument('-j', '--autojoin', action='store_true',
                                dest='autojoin', required=False,
                                help="Automatically join all remote channels")
    parser.add_argument('-o', '--override', action='store_true',
                                dest='overridelocalip', required=False,
                                help='allow non 127. addresses, this is potentially dangerous')

    args = parser.parse_args()
    # Exit if their chosden ip isn't local. User can override with -o if they so dare
    if not args.ip.startswith('127') and not args.overridelocalip:
        exit('supplied ip isn\'t local\nlocalslackirc has no encryption or ' \
                'authentication, it\'s recommended to only allow local connections\n' \
                'you can override this with -o')

    sl_client = slack.Slack(args.tokenfile)
    sl_events = sl_client.events_iter()
    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serversocket.bind((args.ip, args.port))
    serversocket.listen(1)

    poller = select.poll()

    while True:
        s, _ = serversocket.accept()
        ircclient = Client(s, sl_client, nouserlist=args.nouserlist, autojoin=args.autojoin)

        poller.register(s.fileno(), select.POLLIN)
        if sl_client.fileno is not None:
            poller.register(sl_client.fileno, select.POLLIN)

        # Main loop
        timeout = 0.1
        while True:
            s_event = poller.poll(timeout)  # type: List[Tuple[int,int]]
            sl_event = next(sl_events)

            if s_event:
                text = s.recv(1024)
                if len(text) == 0:
                    break
                #FIXME handle the case when there is more to be read
                for i in text.split(b'\n')[:-1]:
                    i = i.strip()
                    if i:
                        ircclient.command(i)

            if sl_event:
                print(sl_event)
                ircclient.slack_event(sl_event)
                timeout = 0.1
            else:
                timeout = 7

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
