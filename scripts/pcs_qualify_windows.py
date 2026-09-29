"""Native, read-only Windows IPv4 witness admission. No network configuration writes.

Structures follow the Windows SDK netioapi.h. Use fixed-width fields (including
UTF-16 arrays), so ABI layout tests also run on Linux. Private identities never
enter witness evidence. Windows 10 1803+ / Windows 11, 64-bit Python only.
"""
import ctypes as c
import ipaddress
import re
import socket
import struct
import sys

U8, U16, U32, U64 = c.c_uint8, c.c_uint16, c.c_uint32, c.c_uint64


class Address(c.Structure):
    # SOCKADDR_INET union: IPv4 begins family/port/address; union size is 28.
    _fields_ = [('family', U16), ('port', U16), ('ip', U8 * 4), ('rest', U32 * 5)]

    @classmethod
    def ipv4(cls, value):
        row = cls()
        row.family = socket.AF_INET
        row.ip[:] = ipaddress.IPv4Address(value).packed
        return row

    def value(self):
        if self.family != socket.AF_INET:
            raise ValueError('windows_address_family')
        return ipaddress.IPv4Address(bytes(self.ip))


class Prefix(c.Structure):
    _fields_ = [('address', Address), ('length', U8)]


class Route(c.Structure):
    _fields_ = [('luid', U64), ('index', U32), ('prefix', Prefix), ('next_hop', Address),
                ('site_prefix', U8), ('valid', U32), ('preferred', U32), ('metric', U32),
                ('protocol', U32), ('loopback', U8), ('autoconfigure', U8),
                ('publish', U8), ('immortal', U8), ('age', U32), ('origin', U32)]


class Unicast(c.Structure):
    _fields_ = [('address', Address), ('luid', U64), ('index', U32),
                ('prefix_origin', U32), ('suffix_origin', U32), ('valid', U32),
                ('preferred', U32), ('prefix_length', U8), ('skip_source', U8),
                ('dad', U32), ('scope', U32), ('created', U64)]


class Interface(c.Structure):
    _fields_ = [('luid', U64), ('index', U32), ('guid', U8 * 16),
                ('alias', U16 * 257), ('description', U16 * 257), ('mac_length', U32),
                ('mac', U8 * 32), ('permanent', U8 * 32)] + [
                    (name, U32) for name in ('mtu', 'type', 'tunnel', 'media',
                        'physical_medium', 'access', 'direction')] + [
                ('flags', U8), ('oper', U32), ('admin', U32), ('connected', U32),
                ('network_guid', U8 * 16), ('connection_type', U32), ('counters', U64 * 20)]


class IPInterface(c.Structure):
    _fields_ = [('family', U16), ('luid', U64), ('index', U32), ('max_reassembly', U32),
                ('identifier', U64), ('min_ra', U32), ('max_ra', U32)] + [
                    (name, U8) for name in ('advertising', 'forwarding', 'weak_send',
                        'weak_receive', 'auto_metric', 'neighbor_detection', 'managed',
                        'other_stateful', 'default_advertisement')] + [
                    (name, U32) for name in ('discovery', 'dad', 'base_reachable',
                        'retransmit', 'pmtu_timeout', 'link_local', 'link_local_timeout')] + [
                ('zones', U32 * 16), ('site_prefix', U32), ('metric', U32), ('mtu', U32),
                ('connected', U8), ('wake', U8), ('neighbor', U8), ('router', U8),
                ('reachable', U32), ('transmit_offload', U8), ('receive_offload', U8),
                ('disable_defaults', U8)]


def interface_index(value):
    if not isinstance(value, str) or not re.fullmatch('[1-9][0-9]{0,7}', value):
        raise ValueError('windows_numeric_interface_index_required')
    index = int(value)
    if index > 0xffffff:
        raise ValueError('windows_interface_index_range')
    return index


def library():
    if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
        raise ValueError('windows_64bit_required')
    # Load the OS DLL only from System32, never from the witness directory.
    dll = c.WinDLL('iphlpapi.dll', winmode=0x800)
    for name, row in (('GetIfEntry2', Interface), ('GetUnicastIpAddressEntry', Unicast),
                      ('GetIpInterfaceEntry', IPInterface)):
        function = getattr(dll, name)
        function.argtypes, function.restype = [c.POINTER(row)], U32
    dll.GetBestRoute2.argtypes = [c.POINTER(U64), U32, c.POINTER(Address),
                                 c.POINTER(Address), U32, c.POINTER(Route), c.POINTER(Address)]
    dll.GetBestRoute2.restype = U32
    return dll


def inspect(target, source, index):
    api = library()
    interface, unicast, policy = Interface(), Unicast(), IPInterface()
    interface.index = unicast.index = policy.index = index
    unicast.address, policy.family = Address.ipv4(source), socket.AF_INET
    for name, row in (('GetIfEntry2', interface), ('GetUnicastIpAddressEntry', unicast),
                      ('GetIpInterfaceEntry', policy)):
        if getattr(api, name)(c.byref(row)):
            raise ValueError('windows_interface_query_failed')
    route, best = Route(), Address()
    local, remote = Address.ipv4(source), Address.ipv4(target)
    # Unconstrained source-specific lookup must select the requested adapter;
    # IP_UNICAST_IF separately pins actual socket egress to that same adapter.
    if api.GetBestRoute2(None, 0, c.byref(local), c.byref(remote), 0, c.byref(route), c.byref(best)):
        raise ValueError('windows_route_query_failed')
    return interface, unicast, policy, route, best


def client_route(target, source, interface):
    index = interface_index(interface)
    dest, local = ipaddress.IPv4Address(target), ipaddress.IPv4Address(source)
    if dest == local or any(a.is_unspecified or a.is_multicast or a.is_loopback or
                           a.is_link_local or a.is_reserved for a in (dest, local)):
        raise ValueError('invalid_lan_addresses')
    nic, address, policy, route, best = inspect(str(dest), str(local), index)
    if (nic.index != index or nic.type not in (6, 71) or nic.tunnel != 0 or
            nic.flags != 5 or nic.oper != 1 or nic.admin != 1 or nic.connected != 1 or
            nic.mac_length != 6 or not any(nic.mac[:6]) or not any(nic.guid) or
            not nic.luid or any(r.index != index or r.luid != nic.luid for r in (address, policy, route)) or
            policy.family != socket.AF_INET or policy.connected != 1 or
            policy.weak_send or policy.weak_receive or policy.forwarding or
            address.address.value() != local or address.dad != 4 or address.skip_source or
            not address.valid or not address.preferred or not 1 <= address.prefix_length <= 30):
        raise ValueError('windows_physical_lan_interface_required')
    subnet = ipaddress.IPv4Network((local, address.prefix_length), strict=False)
    if (dest not in subnet or dest in (subnet.network_address, subnet.broadcast_address) or
            local in (subnet.network_address, subnet.broadcast_address) or
            route.loopback or not route.valid or route.next_hop.value() != ipaddress.IPv4Address('0.0.0.0') or
            route.prefix.length != subnet.prefixlen or route.prefix.address.value() != subnet.network_address or
            best.value() != local):
        raise ValueError('direct_lan_route_required')
    # Private, stable identity; never persisted. Route/adapter replacement aborts.
    return (index, nic.luid, bytes(nic.guid), bytes(nic.mac[:6]), bytes(nic.permanent[:6]), str(subnet))


def pin_socket(sock, interface, kind):
    index = interface_index(interface)
    sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', index))  # IP_UNICAST_IF
    if sock.getsockopt(socket.IPPROTO_IP, 31) != index:  # Readback is host order.
        raise ValueError('windows_socket_interface_unverified')
    if kind == socket.SOCK_DGRAM:
        sock.setsockopt(socket.IPPROTO_IP, 28, 1)  # IP_IFLIST: restrict incoming receipts too.
        sock.setsockopt(socket.IPPROTO_IP, 29, index)  # IP_ADD_IFLIST, host order.
        if (sock.getsockopt(socket.IPPROTO_IP, 28) != 1 or
                sock.getsockopt(socket.IPPROTO_IP, 33, 4) != struct.pack('=I', index)):
            raise ValueError('windows_receipt_interface_unverified')
