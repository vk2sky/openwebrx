import base64
import hashlib
import json
import os
import select

import logging

logger = logging.getLogger(__name__)


class IncompleteRead(Exception):
    pass


class WebSocketConnection(object):
    def __init__(self, handler, messageHandler):
        self.handler = handler
        self.messageHandler = messageHandler
        (self.interruptPipeRecv, self.interruptPipeSend) = os.pipe()
        self.open = True
        my_headers = self.handler.headers.items()
        my_header_keys = list(map(lambda x: x[0], my_headers))
        h_key_exists = lambda x: my_header_keys.count(x)
        h_value = lambda x: my_headers[my_header_keys.index(x)][1]
        if (
            (not h_key_exists("Upgrade"))
            or not (h_value("Upgrade") == "websocket")
            or (not h_key_exists("Sec-WebSocket-Key"))
        ):
            raise WebSocketException
        ws_key = h_value("Sec-WebSocket-Key")
        shakey = hashlib.sha1()
        shakey.update("{ws_key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".format(ws_key=ws_key).encode())
        ws_key_toreturn = base64.b64encode(shakey.digest())
        self.handler.wfile.write(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {0}\r\nCQ-CQ-de: HA5KFU\r\n\r\n".format(
                ws_key_toreturn.decode()
            ).encode()
        )

    def get_header(self, size, opcode):
        ws_first_byte = 0b10000000 | (opcode & 0x0F)
        if size > 2 ** 16 - 1:
            # frame size can be increased up to 2^64 by setting the size to 127
            # anything beyond that would need to be segmented into frames. i don't really think we'll need more.
            return bytes(
                [
                    ws_first_byte,
                    127,
                    (size >> 56) & 0xFF,
                    (size >> 48) & 0xFF,
                    (size >> 40) & 0xFF,
                    (size >> 32) & 0xFF,
                    (size >> 24) & 0xFF,
                    (size >> 16) & 0xFF,
                    (size >> 8) & 0xFF,
                    size & 0xFF,
                ]
            )
        elif size > 125:
            # up to 2^16 can be sent using the extended payload size field by putting the size to 126
            return bytes([ws_first_byte, 126, (size >> 8) & 0xFF, size & 0xFF])
        else:
            # 125 bytes binary message in a single unmasked frame
            return bytes([ws_first_byte, size])

    def send(self, data):
        # convenience
        if type(data) == dict:
            # allow_nan = False disallows NaN and Infinty to be encoded. Browser JSON will not parse them anyway.
            data = json.dumps(data, allow_nan=False)

        # string-type messages are sent as text frames
        if type(data) == str:
            header = self.get_header(len(data), 1)
            data_to_send = header + data.encode("utf-8")
        # anything else as binary
        else:
            header = self.get_header(len(data), 2)
            data_to_send = header + data
        written = self.handler.wfile.write(data_to_send)
        if written != len(data_to_send):
            logger.error("incomplete write! closing socket!")
            self.close()
        else:
            self.handler.wfile.flush()

    def protected_read(self, num):
        data = self.handler.rfile.read(num)
        if len(data) != num:
            raise IncompleteRead()
        return data

    def interrupt(self):
        os.write(self.interruptPipeSend, bytes(0x00))

    def read_loop(self):
        self.open = True
        while self.open:
            try:
                (read, _, _) = select.select([self.interruptPipeRecv, self.handler.rfile], [], [])
                if read[0] == self.handler.rfile:
                    header = self.protected_read(2)
                    opcode = header[0] & 0x0F
                    length = header[1] & 0x7F
                    mask = (header[1] & 0x80) >> 7
                    if length == 126:
                        header = self.protected_read(2)
                        length = (header[0] << 8) + header[1]
                    if mask:
                        masking_key = self.protected_read(4)
                    data = self.protected_read(length)
                    if mask:
                        data = bytes([b ^ masking_key[index % 4] for (index, b) in enumerate(data)])
                    if opcode == 1:
                        message = data.decode("utf-8")
                        self.messageHandler.handleTextMessage(self, message)
                    elif opcode == 2:
                        self.messageHandler.handleBinaryMessage(self, data)
                    elif opcode == 8:
                        logger.debug("websocket close frame received; closing connection")
                        self.open = False
                    else:
                        logger.warning("unsupported opcode: {0}".format(opcode))
            except IncompleteRead:
                logger.warning("incomplete websocket read; closing socket")
                self.open = False

        logger.debug("websocket loop ended; sending close frame")

        try:
            header = self.get_header(0, 8)
            self.handler.wfile.write(header)
            self.handler.wfile.flush()
        except ValueError:
            logger.exception("ValueError while writing close frame:")
        except OSError:
            logger.exception("OSError while writing close frame:")

    def close(self):
        self.open = False
        self.interrupt()


class WebSocketException(Exception):
    pass
