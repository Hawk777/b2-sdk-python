import io

from b2sdk.stream.wrapper import StreamWithLengthWrapper
from b2sdk.stream.base import ReadOnlyMixin


class RangeOfInputStream(ReadOnlyMixin, StreamWithLengthWrapper):
    """
    Wrap a file-like object (read only) and read the selected
    range of the file.
    """

    def __init__(self, stream, offset, length):
        """
        :param stream: a seekable stream
        :param int offset: offset in the stream
        :param int length: max number of bytes to read
        """
        super(RangeOfInputStream, self).__init__(stream, length)
        self.offset = offset
        self.relative_pos = 0
        self.stream.seek(self.offset)

    def seek(self, pos, whence=0):
        """
        Seek to a given position in the stream.

        :param int pos: position in the stream
        """
        if whence != 0:
            # TODO: maybe support other possible values...
            raise io.UnsupportedOperation('only SEEK_SET is supported')
        abs_pos = super(RangeOfInputStream, self).seek(self.offset + pos)
        self.relative_pos = abs_pos - self.offset
        return self.relative_pos

    def tell(self):
        return self.relative_pos

    def read(self, size=None):
        """
        Read data from the stream.

        :param int size: number of bytes to read
        :return: data read from the stream
        """
        remaining = max(0, self.length - self.relative_pos)
        if size is None:
            to_read = remaining
        else:
            to_read = min(size, remaining)
        data = self.stream.read(to_read)
        self.relative_pos += len(data)
        return data


def wrap_with_range(stream, stream_length, range_offset, range_length):
    if range_offset == 0 and range_length == stream_length:
        return stream
    return RangeOfInputStream(stream, range_offset, range_length)
