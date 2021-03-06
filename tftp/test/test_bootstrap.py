'''
@author: shylent
'''
from tftp.bootstrap import (LocalOriginWriteSession, LocalOriginReadSession,
    RemoteOriginReadSession, RemoteOriginWriteSession, TFTPBootstrap)
from tftp.datagram import (ACKDatagram, TFTPDatagramFactory, split_opcode,
    ERR_TID_UNKNOWN, DATADatagram, OACKDatagram, OP_ACK)
from tftp.test.test_sessions import DelayedWriter, FakeTransport, DelayedReader
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import Clock
from twisted.python.compat import intToBytes
from twisted.python.filepath import FilePath
from twisted.python.util import OrderedDict
from twisted.trial import unittest
import shutil
import tempfile
from tftp.session import MAX_BLOCK_SIZE, WriteSession, ReadSession

ReadSession.timeout = (2, 2, 2)
WriteSession.timeout = (2, 2, 2)
RemoteOriginReadSession.timeout = (2, 2, 2)
RemoteOriginWriteSession.timeout = (2, 2, 2)

class MockHandshakeWatchdog(object):

    def __init__(self, when, f, args=None, kwargs=None, _clock=None):
        self._clock = _clock
        self.when = when
        self.f = f
        self.args = args or []
        self.kwargs = kwargs or {}
        if _clock is None:
            self._clock = reactor
        else:
            self._clock = _clock

    def start(self):
        self.wd = self._clock.callLater(self.when, self.f, *self.args, **self.kwargs)

    def cancel(self):
        if self.wd.active():
            self.wd.cancel()

    def active(self):
        return self.wd.active()

class MockSession(object):
    block_size = 512
    timeout = (1, 3, 5)
    tsize = None

# Testing implementation here, but if I don't, I'll have a TON of duplicate code
class TestOptionProcessing(unittest.TestCase):

    def setUp(self):
        self.proto = TFTPBootstrap(('127.0.0.1', 1111), None)

    def test_empty_options(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict())
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, 512)
        self.assertEqual(self.s.timeout, (1, 3, 5))

    def test_blksize(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'blksize':b'8'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, 8)
        self.assertEqual(opts, OrderedDict({b'blksize':b'8'}))

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'blksize':b'foo'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, 512)
        self.assertEqual(opts, OrderedDict())

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'blksize':b'65464'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, MAX_BLOCK_SIZE)
        self.assertEqual(opts, OrderedDict({b'blksize':intToBytes(MAX_BLOCK_SIZE)}))

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'blksize':b'65465'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, 512)
        self.assertEqual(opts, OrderedDict())

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'blksize':b'7'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.block_size, 512)
        self.assertEqual(opts, OrderedDict())

    def test_timeout(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'timeout':b'1'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (1, 1, 1))
        self.assertEqual(opts, OrderedDict({b'timeout':b'1'}))

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'timeout':b'foo'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (1, 3, 5))
        self.assertEqual(opts, OrderedDict())

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'timeout':b'0'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (1, 3, 5))
        self.assertEqual(opts, OrderedDict())

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'timeout':b'255'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (255, 255, 255))
        self.assertEqual(opts, OrderedDict({b'timeout':b'255'}))

        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'timeout':b'256'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (1, 3, 5))
        self.assertEqual(opts, OrderedDict())

    def test_tsize(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'tsize':b'1'}))
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.tsize, 1)
        self.assertEqual(opts, OrderedDict({b'tsize':b'1'}))

    def test_tsize_ignored_when_not_a_number(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'tsize':b'foo'}))
        self.proto.applyOptions(self.s, opts)
        self.assertTrue(self.s.tsize is None)
        self.assertEqual(opts, OrderedDict({}))

    def test_tsize_ignored_when_less_than_zero(self):
        self.s = MockSession()
        opts = self.proto.processOptions(OrderedDict({b'tsize':b'-1'}))
        self.proto.applyOptions(self.s, opts)
        self.assertTrue(self.s.tsize is None)
        self.assertEqual(opts, OrderedDict({}))

    def test_multiple_options(self):
        got_options = OrderedDict()
        got_options[b'timeout'] = b'123'
        got_options[b'blksize'] = b'1024'
        self.s = MockSession()
        opts = self.proto.processOptions(got_options)
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (123, 123, 123))
        self.assertEqual(self.s.block_size, 1024)
        self.assertEqual(list(opts.items()), list(got_options.items()))

        got_options = OrderedDict()
        got_options[b'blksize'] = b'1024'
        got_options[b'timeout'] = b'123'
        self.s = MockSession()
        opts = self.proto.processOptions(got_options)
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (123, 123, 123))
        self.assertEqual(self.s.block_size, 1024)
        self.assertEqual(list(opts.items()), list(got_options.items()))

        got_options = OrderedDict()
        got_options[b'blksize'] = b'1024'
        got_options[b'foobar'] = b'barbaz'
        got_options[b'timeout'] = b'123'
        self.s = MockSession()
        opts = self.proto.processOptions(got_options)
        self.proto.applyOptions(self.s, opts)
        self.assertEqual(self.s.timeout, (123, 123, 123))
        self.assertEqual(self.s.block_size, 1024)
        actual_options = OrderedDict()
        actual_options[b'blksize'] = b'1024'
        actual_options[b'timeout'] = b'123'
        self.assertEqual(list(opts.items()), list(actual_options.items()))


class BootstrapLocalOriginWrite(unittest.TestCase):

    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        self.writer = DelayedWriter(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.ws = LocalOriginWriteSession(('127.0.0.1', 65465), self.writer, _clock=self.clock)
        self.wd = MockHandshakeWatchdog(4, self.ws.timedOut, _clock=self.clock)
        self.ws.timeout_watchdog = self.wd
        self.ws.transport = self.transport

    def test_invalid_tid(self):
        self.ws.startProtocol()
        bad_tid_dgram = ACKDatagram(123)
        self.ws.datagramReceived(bad_tid_dgram.to_wire(), ('127.0.0.1', 1111))

        err_dgram = TFTPDatagramFactory(*split_opcode(self.transport.value()))
        self.assertEqual(err_dgram.errorcode, ERR_TID_UNKNOWN)
        self.addCleanup(self.ws.cancel)
    #test_invalid_tid.skip = 'Will go to another test case'

    def test_local_origin_write_session_handshake_timeout(self):
        self.ws.startProtocol()
        self.clock.advance(5)
        self.assertFalse(self.transport.value())
        self.assertTrue(self.transport.disconnecting)

    def test_local_origin_write_session_handshake_success(self):
        self.ws.session.block_size = 6
        self.ws.startProtocol()
        self.clock.advance(1)
        data_datagram = DATADatagram(1, b'foobar')
        self.ws.datagramReceived(data_datagram.to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), ACKDatagram(1).to_wire())
        self.assertFalse(self.transport.disconnecting)
        self.assertFalse(self.wd.active())
        self.addCleanup(self.ws.cancel)

    def tearDown(self):
        self.temp_dir.remove()

class LocalOriginWriteOptionNegotiation(unittest.TestCase):

    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        self.writer = DelayedWriter(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.ws = LocalOriginWriteSession(('127.0.0.1', 65465), self.writer,
                                          options={b'blksize':b'123'}, _clock=self.clock)
        self.wd = MockHandshakeWatchdog(4, self.ws.timedOut, _clock=self.clock)
        self.ws.timeout_watchdog = self.wd
        self.ws.transport = self.transport


    def test_option_normal(self):
        self.ws.startProtocol()
        self.ws.datagramReceived(OACKDatagram({b'blksize':b'12'}).to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(0.1)
        self.assertEqual(self.ws.session.block_size, WriteSession.block_size)
        self.assertEqual(self.transport.value(), ACKDatagram(0).to_wire())

        self.transport.clear()
        self.ws.datagramReceived(OACKDatagram({b'blksize':b'9'}).to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(0.1)
        self.assertEqual(self.ws.session.block_size, WriteSession.block_size)
        self.assertEqual(self.transport.value(), ACKDatagram(0).to_wire())

        self.transport.clear()
        self.ws.datagramReceived(DATADatagram(1, b'foobarbaz').to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(3)
        self.assertTrue(self.ws.session.started)
        self.clock.advance(0.1)
        self.assertEqual(self.ws.session.block_size, 9)
        self.assertEqual(self.transport.value(), ACKDatagram(1).to_wire())

        self.transport.clear()
        self.ws.datagramReceived(DATADatagram(2, b'asdfghjkl').to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(3)
        self.assertEqual(self.transport.value(), ACKDatagram(2).to_wire())
        self.writer.finish()
        self.assertEqual(self.writer.file_path.open('r').read(), b'foobarbazasdfghjkl')

        self.transport.clear()
        self.ws.datagramReceived(OACKDatagram({b'blksize':b'12'}).to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(0.1)
        self.assertEqual(self.ws.session.block_size, 9)
        self.assertEqual(self.transport.value(), ACKDatagram(0).to_wire())

    def test_option_timeout(self):
        self.ws.startProtocol()
        self.clock.advance(5)
        self.assertTrue(self.transport.disconnecting)

    def tearDown(self):
        self.temp_dir.remove()

class BootstrapRemoteOriginWrite(unittest.TestCase):

    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        self.writer = DelayedWriter(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.ws = RemoteOriginWriteSession(('127.0.0.1', 65465), self.writer, _clock=self.clock)
        self.ws.transport = self.transport
        self.ws.startProtocol()

    @inlineCallbacks
    def test_invalid_tid(self):
        bad_tid_dgram = ACKDatagram(123)
        yield self.ws.datagramReceived(bad_tid_dgram.to_wire(), ('127.0.0.1', 1111))
        err_dgram = TFTPDatagramFactory(*split_opcode(self.transport.value()))
        self.assertEqual(err_dgram.errorcode, ERR_TID_UNKNOWN)
        self.addCleanup(self.ws.cancel)

    def test_remote_origin_write_bootstrap(self):
        # Initial ACK
        ack_datagram_0 = ACKDatagram(0)
        self.clock.advance(0.1)
        self.assertEqual(self.transport.value(), ack_datagram_0.to_wire())
        self.assertFalse(self.transport.disconnecting)

        # Normal exchange
        self.transport.clear()
        d = self.ws.datagramReceived(DATADatagram(1, b'foobar').to_wire(), ('127.0.0.1', 65465))
        def cb(res):
            self.clock.advance(0.1)
            ack_datagram_1 = ACKDatagram(1)
            self.assertEqual(self.transport.value(), ack_datagram_1.to_wire())
            self.assertEqual(self.target.open('r').read(), b'foobar')
            self.assertFalse(self.transport.disconnecting)
            self.addCleanup(self.ws.cancel)
        d.addCallback(cb)
        self.clock.advance(3)
        return d

    def tearDown(self):
        self.temp_dir.remove()


class RemoteOriginWriteOptionNegotiation(unittest.TestCase):

    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        self.writer = DelayedWriter(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.options = OrderedDict()
        self.options[b'blksize'] = b'9'
        self.options[b'tsize'] = b'45'
        self.ws = RemoteOriginWriteSession(
            ('127.0.0.1', 65465), self.writer, options=self.options,
            _clock=self.clock)
        self.ws.transport = self.transport

    def test_option_normal(self):
        self.ws.startProtocol()
        self.clock.advance(0.1)
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)
        self.clock.advance(3)
        self.assertEqual(self.transport.value(), oack_datagram * 2)

        self.transport.clear()
        self.ws.datagramReceived(DATADatagram(1, b'foobarbaz').to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), ACKDatagram(1).to_wire())
        self.assertEqual(self.ws.session.block_size, 9)

        self.transport.clear()
        self.ws.datagramReceived(DATADatagram(2, b'smthng').to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), ACKDatagram(2).to_wire())
        self.clock.pump((1,)*10)
        self.writer.finish()
        self.assertEqual(self.writer.file_path.open('r').read(), b'foobarbazsmthng')
        self.assertTrue(self.transport.disconnecting)

    def test_option_timeout(self):
        self.ws.startProtocol()
        self.clock.advance(0.1)
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(3)
        self.assertEqual(self.transport.value(), oack_datagram * 2)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(2)
        self.assertEqual(self.transport.value(), oack_datagram * 3)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(2)
        self.assertEqual(self.transport.value(), oack_datagram * 3)
        self.assertTrue(self.transport.disconnecting)

    def test_option_tsize(self):
        # A tsize option sent as part of a write session is recorded.
        self.ws.startProtocol()
        self.clock.advance(0.1)
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)
        self.assertFalse(self.transport.disconnecting)
        self.assertIsInstance(self.ws.session, WriteSession)
        # Options are not applied to the WriteSession until the first DATA
        # datagram is received,
        self.assertTrue(self.ws.session.tsize is None)
        self.ws.datagramReceived(
            DATADatagram(1, b'foobarbaz').to_wire(), ('127.0.0.1', 65465))
        # The tsize option has been applied to the WriteSession.
        self.assertEqual(45, self.ws.session.tsize)

    def tearDown(self):
        self.temp_dir.remove()


class BootstrapLocalOriginRead(unittest.TestCase):
    test_data = b"""line1
line2
anotherline"""
    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        with self.target.open('wb') as temp_fd:
            temp_fd.write(self.test_data)
        self.reader = DelayedReader(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.rs = LocalOriginReadSession(('127.0.0.1', 65465), self.reader, _clock=self.clock)
        self.wd = MockHandshakeWatchdog(4, self.rs.timedOut, _clock=self.clock)
        self.rs.timeout_watchdog = self.wd
        self.rs.transport = self.transport
        self.rs.startProtocol()

    def test_invalid_tid(self):
        data_datagram = DATADatagram(1, b'foobar')
        self.rs.datagramReceived(data_datagram, ('127.0.0.1', 11111))
        self.clock.advance(0.1)
        err_dgram = TFTPDatagramFactory(*split_opcode(self.transport.value()))
        self.assertEqual(err_dgram.errorcode, ERR_TID_UNKNOWN)
        self.addCleanup(self.rs.cancel)

    def test_local_origin_read_session_handshake_timeout(self):
        self.clock.advance(5)
        self.assertFalse(self.transport.value())
        self.assertTrue(self.transport.disconnecting)

    def test_local_origin_read_session_handshake_success(self):
        self.clock.advance(1)
        ack_datagram = ACKDatagram(0)
        self.rs.datagramReceived(ack_datagram.to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(2)
        self.assertTrue(self.transport.value())
        self.assertFalse(self.transport.disconnecting)
        self.assertFalse(self.wd.active())
        self.addCleanup(self.rs.cancel)

    def tearDown(self):
        self.temp_dir.remove()


class LocalOriginReadOptionNegotiation(unittest.TestCase):
    test_data = b"""line1
line2
anotherline"""
    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        with self.target.open('wb') as temp_fd:
            temp_fd.write(self.test_data)
        self.reader = DelayedReader(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.rs = LocalOriginReadSession(('127.0.0.1', 65465), self.reader, _clock=self.clock)
        self.wd = MockHandshakeWatchdog(4, self.rs.timedOut, _clock=self.clock)
        self.rs.timeout_watchdog = self.wd
        self.rs.transport = self.transport

    def test_option_normal(self):
        self.rs.startProtocol()
        self.rs.datagramReceived(OACKDatagram({b'blksize':b'9'}).to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(0.1)
        self.assertEqual(self.rs.session.block_size, 9)
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), DATADatagram(1, self.test_data[:9]).to_wire())

        self.rs.datagramReceived(OACKDatagram({b'blksize':b'12'}).to_wire(), ('127.0.0.1', 65465))
        self.clock.advance(0.1)
        self.assertEqual(self.rs.session.block_size, 9)

        self.transport.clear()
        self.rs.datagramReceived(ACKDatagram(1).to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), DATADatagram(2, self.test_data[9:18]).to_wire())

        self.addCleanup(self.rs.cancel)

    def test_local_origin_read_option_timeout(self):
        self.rs.startProtocol()
        self.clock.advance(5)
        self.assertTrue(self.transport.disconnecting)

    def tearDown(self):
        self.temp_dir.remove()


class BootstrapRemoteOriginRead(unittest.TestCase):
    test_data = b"""line1
line2
anotherline"""
    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        with self.target.open('wb') as temp_fd:
            temp_fd.write(self.test_data)
        self.reader = DelayedReader(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.rs = RemoteOriginReadSession(('127.0.0.1', 65465), self.reader, _clock=self.clock)
        self.rs.transport = self.transport

    @inlineCallbacks
    def test_invalid_tid(self):
        self.rs.startProtocol()
        data_datagram = DATADatagram(1, b'foobar')
        yield self.rs.datagramReceived(data_datagram, ('127.0.0.1', 11111))
        err_dgram = TFTPDatagramFactory(*split_opcode(self.transport.value()))
        self.assertEqual(err_dgram.errorcode, ERR_TID_UNKNOWN)
        self.addCleanup(self.rs.cancel)

    def test_remote_origin_read_bootstrap(self):
        # First datagram
        self.rs.session.block_size = 5
        self.rs.startProtocol()
        self.clock.pump((1,)*3)

        data_datagram_1 = DATADatagram(1, self.test_data[:5])

        self.assertEqual(self.transport.value(), data_datagram_1.to_wire())
        self.assertFalse(self.transport.disconnecting)

        # Normal exchange continues
        self.transport.clear()
        self.rs.datagramReceived(ACKDatagram(1).to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        data_datagram_2 = DATADatagram(2, self.test_data[5:10])
        self.assertEqual(self.transport.value(), data_datagram_2.to_wire())
        self.assertFalse(self.transport.disconnecting)
        self.addCleanup(self.rs.cancel)

    def test_remote_origin_read_session_not_started_rollover(self):
        # if a rollover is done, we reach blocknum 0 again. But this time
        # session is already started.
        # Here we test the case where rollover has not happened yet

        data_datagram = DATADatagram(1, self.test_data[:5])
        data_datagram.opcode = OP_ACK
        data_datagram.blocknum = 0
        self.rs.session.block_size = 5
        self.clock.pump((1,)*3)

        self.rs.session.transport = b"Fake"
        self.rs.session.started = False
        self.rs._datagramReceived(data_datagram)

        self.assertEqual(self.rs.session.started, True)
        self.assertEqual(self.rs.session.transport, self.rs.transport)

        self.addCleanup(self.rs.cancel)

    def test_remote_origin_read_session_started_rollover(self):
        # if a rollover is done, we reach blocknum 0 again. But this time
        # session is already started.
        # Here we test the case where rollover has already happened
        data_datagram = DATADatagram(1, self.test_data[:5])
        data_datagram.opcode = OP_ACK
        data_datagram.blocknum = 0
        self.rs.session.block_size = 5
        self.rs.startProtocol()
        self.clock.pump((1,)*3)

        self.assertEqual(
            self.rs._datagramReceived(data_datagram),
            self.rs.session.datagramReceived(data_datagram))
        self.addCleanup(self.rs.cancel)

    def tearDown(self):
        self.temp_dir.remove()


class RemoteOriginReadOptionNegotiation(unittest.TestCase):
    test_data = b"""line1
line2
anotherline"""
    port = 65466

    def setUp(self):
        self.clock = Clock()
        self.temp_dir = FilePath(tempfile.mkdtemp()).asBytesMode()
        self.target = self.temp_dir.child(b'foo')
        with self.target.open('wb') as temp_fd:
            temp_fd.write(self.test_data)
        self.reader = DelayedReader(self.target, _clock=self.clock, delay=2)
        self.transport = FakeTransport(hostAddress=('127.0.0.1', self.port))
        self.options = OrderedDict()
        self.options[b'blksize'] = b'9'
        self.options[b'tsize'] = b'34'
        self.rs = RemoteOriginReadSession(('127.0.0.1', 65465), self.reader,
                                          options=self.options, _clock=self.clock)
        self.rs.transport = self.transport

    def test_option_normal(self):
        self.rs.startProtocol()
        self.clock.advance(0.1)
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)
        self.clock.advance(3)
        self.assertEqual(self.transport.value(), oack_datagram * 2)

        self.transport.clear()
        self.rs.datagramReceived(ACKDatagram(0).to_wire(), ('127.0.0.1', 65465))
        self.clock.pump((1,)*3)
        self.assertEqual(self.transport.value(), DATADatagram(1, self.test_data[:9]).to_wire())

        self.addCleanup(self.rs.cancel)

    def test_option_timeout(self):
        self.rs.startProtocol()
        self.clock.advance(0.1)
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(3)
        self.assertEqual(self.transport.value(), oack_datagram * 2)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(2)
        self.assertEqual(self.transport.value(), oack_datagram * 3)
        self.assertFalse(self.transport.disconnecting)

        self.clock.advance(2)
        self.assertEqual(self.transport.value(), oack_datagram * 3)
        self.assertTrue(self.transport.disconnecting)

    def test_option_tsize(self):
        # A tsize option of 0 sent as part of a read session prompts a tsize
        # response with the actual size of the file.
        self.options[b'tsize'] = b'0'
        self.rs.startProtocol()
        self.clock.advance(0.1)
        self.transport.clear()
        self.clock.advance(3)
        # The response contains the size of the test data.
        self.options[b'tsize'] = intToBytes(len(self.test_data))
        oack_datagram = OACKDatagram(self.options).to_wire()
        self.assertEqual(self.transport.value(), oack_datagram)

    def tearDown(self):
        self.temp_dir.remove()
