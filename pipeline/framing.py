"""Length-prefixed JSON framing for asyncio streams.

Used by both PipelineServer and PipelineClient for Unix socket communication.
Each message is: 4-byte big-endian length prefix + JSON bytes.
"""

import asyncio
import json
import struct


class JsonFramer:
    """Read/write length-prefixed JSON over an asyncio stream."""

    HEADER = struct.Struct("!I")  # 4-byte unsigned big-endian

    @staticmethod
    async def read_message(reader: asyncio.StreamReader) -> dict:
        """Read one framed JSON message from the stream.

        Raises ConnectionError if the stream is closed.
        """
        header = await reader.readexactly(JsonFramer.HEADER.size)
        if not header:
            raise ConnectionError("Connection closed")
        (length,) = JsonFramer.HEADER.unpack(header)
        data = await reader.readexactly(length)
        return json.loads(data)

    @staticmethod
    async def write_message(writer: asyncio.StreamWriter, msg: dict) -> None:
        """Write one framed JSON message to the stream."""
        payload = json.dumps(msg, separators=(",", ":")).encode()
        header = JsonFramer.HEADER.pack(len(payload))
        writer.write(header + payload)
        await writer.drain()
