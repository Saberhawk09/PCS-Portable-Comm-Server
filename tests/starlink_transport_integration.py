"""Run only in a disposable Linux VM with the optional Starlink venv.

Uses an ephemeral loopback fake gRPC device. No real dish or power action.
Requires CAP_NET_RAW for the real SO_BINDTODEVICE relay.
"""
from concurrent import futures
from pathlib import Path
import sys
from unittest.mock import patch

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from grpc_reflection.v1alpha import reflection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_starlink as sl


def main():
    if sys.argv[1:] != ['--isolated-vm']:
        raise SystemExit('Requires explicit --isolated-vm')
    proto = descriptor_pb2.FileDescriptorProto(name='fake_device.proto', package='SpaceX.API.Device', syntax='proto3')

    def message(name, fields=()):
        item = proto.message_type.add(name=name)
        for index, (field, kind, target) in enumerate(fields, 1):
            value = item.field.add(name=field, number=index, type=kind, label=1)
            if target:
                value.type_name = '.SpaceX.API.Device.' + target

    message('Empty')
    message('Info', [('id', 9, '')])
    message('State', [('uptime_s', 4, '')])
    message('Status', [('device_info', 11, 'Info'), ('device_state', 11, 'State'),
                       ('pop_ping_latency_ms', 2, ''), ('pop_ping_drop_rate', 2, '')])
    message('Request', [('get_status', 11, 'Empty'), ('reboot', 11, 'Empty')])
    message('Response', [('dish_get_status', 11, 'Status'), ('reboot', 11, 'Empty')])
    service = proto.service.add(name='Device')
    service.method.add(name='Handle', input_type='.SpaceX.API.Device.Request', output_type='.SpaceX.API.Device.Response')
    pool = descriptor_pool.Default()
    pool.Add(proto)
    request = message_factory.GetMessageClass(pool.FindMessageTypeByName('SpaceX.API.Device.Request'))
    response = message_factory.GetMessageClass(pool.FindMessageTypeByName('SpaceX.API.Device.Response'))
    calls = []

    def handle(value, context):
        if value.HasField('reboot'):
            calls.append('reboot')
            return response(reboot={})
        calls.append('status')
        return response(dish_get_status={'device_info': {'id': 'fixture-mini'},
            'device_state': {'uptime_s': 123}, 'pop_ping_latency_ms': 31.25})

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler('SpaceX.API.Device.Device', {
        'Handle': grpc.unary_unary_rpc_method_handler(handle, request_deserializer=request.FromString,
                                                       response_serializer=response.SerializeToString)}),))
    reflection.enable_server_reflection(('SpaceX.API.Device.Device', reflection.SERVICE_NAME), server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    cfg = dict(enabled=True, allow_reboot=True, paired_device_id='fixture-mini')
    try:
        with patch.object(sl, 'TARGET', ('127.0.0.1', port)), patch.object(sl, 'resolve_interface', return_value='lo'), patch.object(sl, 'load_config', return_value=cfg):
            value = sl.worker('status')
            assert value['uptime_seconds'] == 123, value
            assert value['latency_ms'] == 31.25, value
            assert value['packet_loss_percent'] == 0, value
            assert 'fixture-mini' not in str(value), value
            assert calls == ['status'], calls
            cfg['paired_device_id'] = 'wrong'
            try:
                sl.worker('reboot')
                raise AssertionError('Wrong identity accepted')
            except ValueError as error:
                assert 'identity mismatch' in str(error)
            assert calls == ['status', 'status'], calls
            cfg['paired_device_id'] = 'fixture-mini'
            assert sl.worker('reboot')['result'] == 'request_accepted'
            assert calls == ['status', 'status', 'status', 'reboot'], calls
        print('PASS: real reflection, bound relay, zero/uint64 decoding, privacy, identity refusal, one fake reboot')
    finally:
        server.stop(0).wait()


if __name__ == '__main__':
    main()
