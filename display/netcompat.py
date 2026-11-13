# Wi-Fi for boards with Wi-Fi built in (Matrix Portal S3 and other ESP32-S2/S3 boards).
#
# flight.py was written for the Matrix Portal M4, which talks to a separate ESP32 Wi-Fi chip
# through adafruit_esp32spi. NativeWifi offers the handful of ESP_SPIcontrol methods flight.py
# uses, on top of CircuitPython's built-in `wifi` and `socketpool`, so the same flight.py runs
# on both boards. Sockets are numbered like the ESP32's, and reads are buffered so that
# socket_available() works (socketpool has no such call).

import struct
import time

import socketpool
import wifi

_EAGAIN = 11


class _Info:
    def __init__(self, rssi):
        self.rssi = rssi


class NativeWifi:
    TCP_MODE = 0
    UDP_MODE = 1

    def __init__(self):
        self.pool = socketpool.SocketPool(wifi.radio)
        self._socks = {}   # number -> [socket, pending bytes, closed by the other end, is UDP]
        self._next = 0
        self._rx = bytearray(512)
        try:  # keep the radio awake: its power-saving naps upset the LED panel's timing
            wifi.radio.power_management = wifi.PowerManagement.NONE
        except (AttributeError, NameError):
            pass

    # --- connection ----------------------------------------------------------------
    @property
    def is_connected(self):
        return wifi.radio.connected

    def connect_AP(self, ssid, password):  # noqa: N802 - same name as adafruit_esp32spi
        wifi.radio.connect(ssid, password, timeout=15)  # raises ConnectionError on failure

    def disconnect(self):
        try:
            wifi.radio.stop_station()
        except AttributeError:
            pass

    def reset(self):
        """Turn the radio off and on (the next loop reconnects)."""
        for name in list(self._socks):
            self.socket_close(name)
        wifi.radio.enabled = False
        time.sleep(0.5)
        wifi.radio.enabled = True

    @property
    def ip_address(self):
        return wifi.radio.ipv4_address

    @staticmethod
    def pretty_ip(ip):
        return str(ip) if ip is not None else ""

    @property
    def ap_info(self):
        info = wifi.radio.ap_info
        return _Info(info.rssi if info is not None else 0)

    def get_host_by_name(self, host):
        return self.pool.getaddrinfo(host, 80)[0][4][0]

    def get_time(self):
        """Seconds since 1970 (UTC) from an internet time server, as a 1-tuple like the ESP32's."""
        sock = self.pool.socket(self.pool.AF_INET, self.pool.SOCK_DGRAM)
        try:
            sock.settimeout(3)
            packet = bytearray(48)
            packet[0] = 0x1B  # SNTP request
            addr = self.pool.getaddrinfo("pool.ntp.org", 123)[0][4]
            sock.sendto(packet, addr)
            sock.recvfrom_into(packet)
            secs = struct.unpack_from("!I", packet, 40)[0] - 2208988800  # 1900 -> 1970
            if secs <= 0:
                raise OSError("bad time from NTP")
            return (secs,)
        finally:
            sock.close()

    # --- sockets ---------------------------------------------------------------------
    def get_socket(self):
        self._next += 1
        self._socks[self._next] = [None, b"", False, False]
        return self._next

    @staticmethod
    def _addr(dest):
        if isinstance(dest, (bytes, bytearray)):
            return "%d.%d.%d.%d" % tuple(dest)
        return dest

    def socket_connect(self, num, dest, port, conn_mode=TCP_MODE):
        s = self.pool.socket(self.pool.AF_INET, self.pool.SOCK_STREAM)
        s.settimeout(4)
        try:
            s.connect((self._addr(dest), port))
        except Exception:
            s.close()
            raise
        s.settimeout(0)
        self._socks[num][0] = s

    def start_server(self, port, num, conn_mode=UDP_MODE):
        s = self.pool.socket(self.pool.AF_INET, self.pool.SOCK_DGRAM)
        s.bind(("0.0.0.0", port))
        s.settimeout(0)
        self._socks[num] = [s, b"", False, True]

    def socket_write(self, num, data):
        s = self._socks[num][0]
        s.settimeout(4)
        mv = memoryview(data)
        sent = 0
        while sent < len(mv):
            sent += s.send(mv[sent:])
        s.settimeout(0)

    def socket_available(self, num):
        entry = self._socks[num]
        s = entry[0]
        if s is not None and not entry[2]:
            try:
                if entry[3]:
                    n = s.recvfrom_into(self._rx)[0]
                else:
                    n = s.recv_into(self._rx)
                    if n == 0:
                        entry[2] = True  # the other end closed the connection
                if n:
                    entry[1] += bytes(self._rx[:n])
            except OSError as e:
                if e.errno != _EAGAIN:  # EAGAIN = nothing waiting yet
                    entry[2] = True
        return len(entry[1])

    def socket_read(self, num, size):
        entry = self._socks[num]
        out, entry[1] = entry[1][:size], entry[1][size:]
        return out

    def socket_status(self, num):
        entry = self._socks.get(num)
        return 0 if entry is None or entry[2] else 4  # 4 = ESTABLISHED, like the ESP32

    def socket_close(self, num):
        entry = self._socks.pop(num, None)
        if entry and entry[0] is not None:
            entry[0].close()
