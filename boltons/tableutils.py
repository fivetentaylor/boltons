# -*- coding: utf-8 -*-
"""If there is one recurring theme in ``boltons``, it is that Python
has excellent datastructures that constitute a good foundation for
most quick manipulations, as well as building applications. However,
Python usage has grown much faster than builtin data structure
power. Python has a growing need for more advanced general-purpose
data structures which behave intuitively.

The :class:`Table` class is one example. When handed one- or
two-dimensional data, it can provide useful, if basic, text and HTML
renditions of small to medium sized data. It also heuristically
handles recursive data of various formats (lists, dicts, namedtuples,
objects).

For more advanced :class:`Table`-style manipulation check out the
`pandas`_ DataFrame.

.. _pandas: http://pandas.pydata.org/

"""

from __future__ import print_function

import cgi
import types
from itertools import islice
from collections import Sequence, Mapping, MutableSequence
try:
    string_types, integer_types = (str, unicode), (int, long)
except:
    # Python 3 compat
    unicode = str
    string_types, integer_types = (str, bytes), (int,)

try:
    from typeutils import make_sentinel
    _MISSING = make_sentinel(var_name='_MISSING')
except ImportError:
    _MISSING = object()

"""
Some idle feature thoughts:

* shift around column order without rearranging data
* gotta make it so you can add additional items, not just initialize with
* maybe a shortcut would be to allow adding of Tables to other Tables
* what's the perf of preallocating lists and overwriting items versus
  starting from empty?
* is it possible to effectively tell the difference between when a
  Table is from_data()'d with a single row (list) or with a list of lists?
* CSS: white-space pre-line or pre-wrap maybe?
* Would be nice to support different backends (currently uses lists
  exclusively). Sometimes large datasets come in list-of-dicts and
  list-of-tuples format and it's desirable to cut down processing overhead.

TODO: make iterable on rows?
"""

__all__ = ['Table']


def to_text(obj, maxlen=None):
    try:
        text = unicode(obj)
    except:
        try:
            text = unicode(repr(obj))
        except:
            text = unicode(object.__repr__(obj))
    if maxlen and len(text) > maxlen:
        text = text[:maxlen - 3] + '...'
        # TODO: inverse of ljust/rjust/center
    return text


def escape_html(obj, maxlen=None):
    text = to_text(obj, maxlen=maxlen)
    return cgi.escape(text, quote=True)


_DNR = set((type(None), bool, complex, float,
            type(NotImplemented), slice,
            types.FunctionType, types.MethodType, types.BuiltinFunctionType,
            types.GeneratorType) + string_types + integer_types)


class UnsupportedData(TypeError):
    pass


class InputType(object):
    def __init__(self, *a, **kw):
        pass

    def get_entry_seq(self, data_seq, headers):
        return [self.get_entry(entry, headers) for entry in data_seq]


class DictInputType(InputType):
    def check_type(self, obj):
        return isinstance(obj, Mapping)

    def guess_headers(self, obj):
        return obj.keys()

    def get_entry(self, obj, headers):
        return [obj.get(h) for h in headers]

    def get_entry_seq(self, obj, headers):
        return [[ci.get(h) for h in headers] for ci in obj]


class ObjectInputType(InputType):
    def check_type(self, obj):
        return type(obj) not in _DNR and hasattr(obj, '__class__')

    def guess_headers(self, obj):
        headers = []
        for attr in dir(obj):
            # an object's __dict__ could technically have non-string keys
            try:
                val = getattr(obj, attr)
            except:
                # seen on greenlet: `run` shows in dir() but raises
                # AttributeError. Also properties misbehave.
                continue
            if callable(val):
                continue
            headers.append(attr)
        return headers

    def get_entry(self, obj, headers):
        values = []
        for h in headers:
            try:
                values.append(getattr(obj, h))
            except:
                values.append(None)
        return values


# might be better to hardcode list support since it's so close to the
# core or might be better to make this the copy-style from_* importer
# and have the non-copy style be hardcoded in __init__
class ListInputType(InputType):
    def check_type(self, obj):
        return isinstance(obj, MutableSequence)

    def guess_headers(self, obj):
        return None

    def get_entry(self, obj, headers):
        return obj

    def get_entry_seq(self, obj_seq, headers):
        return obj_seq


class TupleInputType(InputType):
    def check_type(self, obj):
        return isinstance(obj, tuple)

    def guess_headers(self, obj):
        return None

    def get_entry(self, obj, headers):
        return list(obj)

    def get_entry_seq(self, obj_seq, headers):
        return [list(t) for t in obj_seq]


class NamedTupleInputType(InputType):
    def check_type(self, obj):
        return hasattr(obj, '_fields') and isinstance(obj, tuple)

    def guess_headers(self, obj):
        return list(obj._fields)

    def get_entry(self, obj, headers):
        return [getattr(obj, h, None) for h in headers]

    def get_entry_seq(self, obj_seq, headers):
        return [[getattr(obj, h, None) for h in headers] for obj in obj_seq]


class Table(object):
    """
    This Table class is meant to be simple, low-overhead, and extensible. Its
    most common use would be for translation between in-memory data
    structures and serialization formats, such as HTML and console-ready text.

    As such, it stores data in list-of-lists format, and *does not* copy
    lists passed in. It also reserves the right to modify those lists in a
    "filling" process, whereby short lists are extended to the width of
    the table (usually determined by number of headers). This greatly
    reduces overhead and processing/validation that would have to occur
    otherwise.

    General description of headers behavior:

    Headers describe the columns, but are not part of the data, however,
    if the *headers* argument is omitted, Table tries to infer header
    names from the data. It is possible to have a table with no headers,
    just pass in ``headers=None``.

    Supported inputs:

    * :class:`list` of :class:`list` objects
    * :class:`dict` (list/single)
    * :class:`object` (list/single)
    * :class:`collections.namedtuple` (list/single)
    * TODO: DB API cursor?
    * TODO: json

    Supported outputs:

    * HTML
    * Pretty text (also usable as GF Markdown)
    * TODO: CSV
    * TODO: json
    * TODO: json lines

    To minimize resident size, the Table data is stored as a list of lists.
    """

    # order definitely matters here
    _input_types = [DictInputType(), ListInputType(),
                    NamedTupleInputType(), TupleInputType(),
                    ObjectInputType()]

    _html_tr, _html_tr_close = '<tr>', '</tr>'
    _html_th, _html_th_close = '<th>', '</th>'
    _html_td, _html_td_close = '<td>', '</td>'
    # _html_thead, _html_thead_close = '<thead>', '</thead>'
    # _html_tfoot, _html_tfoot_close = '<tfoot>', '</tfoot>'
    _html_table_tag, _html_table_tag_close = '<table>', '</table>'

    def __init__(self, data=None, headers=_MISSING):
        if headers is _MISSING:
            headers = []
            if data:
                headers, data = list(data[0]), islice(data, 1, None)
        self.headers = headers or []
        self._data = []
        self._width = 0

        self.extend(data)

    def extend(self, data):
        """
        Append the given data to the end of the Table.
        """
        if not data:
            return
        self._data.extend(data)
        self._set_width()
        self._fill()

    def _set_width(self, reset=False):
        if reset:
            self._width = 0
        if self._width:
            return
        if self.headers:
            self._width = len(self.headers)
            return
        self._width = max([len(d) for d in self._data])

    def _fill(self):
        width, filler = self._width, [None]
        if not width:
            return
        for d in self._data:
            rem = width - len(d)
            if rem > 0:
                d.extend(filler * rem)
        return

    @classmethod
    def from_dict(cls, data, headers=_MISSING, max_depth=1):
        """Create a Table from a :class:`dict`. Operates the same as
        :meth:`from_data`, but forces interpretation of the data as a
        Mapping.
        """
        return cls.from_data(data=data, headers=headers,
                             max_depth=max_depth, _data_type=DictInputType())

    @classmethod
    def from_list(cls, data, headers=_MISSING, max_depth=1):
        """Create a Table from a :class:`list`. Operates the same as
        :meth:`from_data`, but forces the interpretation of the data
        as a Sequence.
        """
        return cls.from_data(data=data, headers=headers,
                             max_depth=max_depth, _data_type=ListInputType())

    @classmethod
    def from_object(cls, data, headers=_MISSING, max_depth=1):
        """Create a Table from an :class:`object`. Operates the same as
        :meth:`from_data`, but forces the interpretation of the data
        as an object. May be useful for some :class:`dict` and
        :class:`list` subtypes.
        """
        return cls.from_data(data=data, headers=headers,
                             max_depth=max_depth, _data_type=ObjectInputType())

    @classmethod
    def from_data(cls, data, headers=_MISSING, max_depth=1, _data_type=None):
        """Create a Table from any supported data, heuristically
        selecting how to represent the data in Table format.

        Args:
            data (object): Any object or iterable with data to be
                imported to the Table.

            headers (iterable): An iterable of headers to be matched
                to the data. If not explicitly passed, headers will be
                guessed for certain datatypes.

            max_depth (int): The level to which nested Tables should
                be created (default: 1).

            _data_type (InputType subclass): For advanced use cases,
                do not guess the type of the input data, use this data
                type instead.
        """
        # TODO: seen/cycle detection/reuse ?
        # maxdepth follows the same behavior as find command
        # i.e., it doesn't work if max_depth=0 is passed in
        if max_depth < 1:
            return cls(headers=headers)  # return data instead?
        is_seq = isinstance(data, Sequence)
        if is_seq:
            if not data:
                return cls(headers=headers)
            to_check = data[0]
            if not _data_type:
                for it in cls._input_types:
                    if it.check_type(to_check):
                        _data_type = it
                        break
                else:
                    # not particularly happy about this rewind-y approach
                    is_seq = False
                    to_check = data
        else:
            if type(data) in _DNR:
                # hmm, got scalar data.
                # raise an exception or make an exception, nahmsayn?
                return Table([[data]], headers=headers)
            to_check = data
        if not _data_type:
            for it in cls._input_types:
                if it.check_type(to_check):
                    _data_type = it
                    break
            else:
                raise UnsupportedData('unsupported data type %r'
                                      % type(data))
        if headers is _MISSING:
            headers = _data_type.guess_headers(to_check)
        if is_seq:
            entries = _data_type.get_entry_seq(data, headers)
        else:
            entries = [_data_type.get_entry(data, headers)]
        if max_depth > 1:
            new_max_depth = max_depth - 1
            for i, entry in enumerate(entries):
                for j, cell in enumerate(entry):
                    if type(cell) in _DNR:
                        # optimization to avoid function overhead
                        continue
                    try:
                        entries[i][j] = cls.from_data(cell,
                                                      max_depth=new_max_depth)
                    except UnsupportedData:
                        continue
        return cls(entries, headers=headers)

    def __len__(self):
        return len(self._data)

    def __getitem__(self, idx):
        return self._data[idx]

    def __repr__(self):
        cn = self.__class__.__name__
        if self.headers:
            return '%s(headers=%r, data=%r)' % (cn, self.headers, self._data)
        else:
            return '%s(%r)' % (cn, self._data)

    def to_html(self, orientation=None, wrapped=True,
                with_headers=True, with_newlines=True, max_depth=1):
        """Render this Table to HTML. Configure the structure of Table
        HTML by subclassing and overriding ``_html_*`` class
        attributes.

        Args:
            orientation (str): one of 'auto', 'horizontal', or
                'vertical' (or the first letter of any of
                those). Default 'auto'.
            wrapped (bool): whether or not to include the wrapping
                '<table></table>' tags. Default ``True``, set to
                ``False`` if appending multiple Table outputs or an
                otherwise customized HTML wrapping tag is needed.
            with_newlines (bool): Set to ``True`` if output should
                include added newlines to make the HTML more
                readable. Default ``False``.
            max_depth (int): Indicate how deeply to nest HTML tables
                before simply reverting to :func:`repr`-ing the nested
                data.

        Returns:
            A text string of the HTML of the rendered table.
        """
        lines = []
        headers = []
        if with_headers and self.headers:
            headers.extend(self.headers)
            headers.extend([None] * (self._width - len(self.headers)))
        if wrapped:
            lines.append(self._html_table_tag)
        orientation = orientation or 'auto'
        ol = orientation[0].lower()
        if ol == 'a':
            ol = 'h' if len(self) > 1 else 'v'
        if ol == 'h':
            self._add_horizontal_html_lines(lines, headers=headers,
                                            max_depth=max_depth)
        elif ol == 'v':
            self._add_vertical_html_lines(lines, headers=headers,
                                          max_depth=max_depth)
        else:
            raise ValueError("expected one of 'auto', 'vertical', or"
                             " 'horizontal', not %r" % orientation)
        if wrapped:
            lines.append(self._html_table_tag_close)
        sep = '\n' if with_newlines else ''
        return sep.join(lines)

    def _add_horizontal_html_lines(self, lines, headers, max_depth):
        esc = escape_html
        new_depth = max_depth - 1 if max_depth > 1 else max_depth
        if max_depth > 1:
            new_depth = max_depth - 1
        if headers:
            _thth = self._html_th_close + self._html_th
            lines.append(self._html_tr + self._html_th +
                         _thth.join([esc(h) for h in headers]) +
                         self._html_th_close + self._html_tr_close)
        trtd, _tdtd, _td_tr = (self._html_tr + self._html_td,
                               self._html_td_close + self._html_td,
                               self._html_td_close + self._html_tr_close)
        for row in self._data:
            if max_depth > 1:
                _fill_parts = []
                for cell in row:
                    if isinstance(cell, Table):
                        _fill_parts.append(cell.to_html(max_depth=new_depth))
                    else:
                        _fill_parts.append(esc(cell))
            else:
                _fill_parts = [esc(c) for c in row]
            lines.append(''.join([trtd, _tdtd.join(_fill_parts), _td_tr]))

    def _add_vertical_html_lines(self, lines, headers, max_depth):
        esc = escape_html
        new_depth = max_depth - 1 if max_depth > 1 else max_depth
        tr, th, _th = self._html_tr, self._html_th, self._html_th_close
        td, _tdtd = self._html_td, self._html_td_close + self._html_td
        _td_tr = self._html_td_close + self._html_tr_close
        for i in range(self._width):
            line_parts = [tr]
            if headers:
                line_parts.extend([th, esc(headers[i]), _th])
            if max_depth > 1:
                new_depth = max_depth - 1
                _fill_parts = []
                for row in self._data:
                    cell = row[i]
                    if isinstance(cell, Table):
                        _fill_parts.append(cell.to_html(max_depth=new_depth))
                    else:
                        _fill_parts.append(esc(row[i]))
            else:
                _fill_parts = [esc(row[i]) for row in self._data]
            line_parts.extend([td, _tdtd.join(_fill_parts), _td_tr])
            lines.append(''.join(line_parts))

    def to_text(self, with_headers=True, maxlen=None):
        """Get the Table's textual representation. Only works well
        for Tables with non-recursive data.

        Args:
            with_headers (bool): Whether to include a header row at the top.
            maxlen (int): Max length of data in each cell.
        """
        # TODO: verify this works for markdown
        lines = []
        widths = []
        headers = list(self.headers)
        text_data = [[to_text(cell, maxlen=maxlen) for cell in row]
                     for row in self._data]
        for idx in range(self._width):
            cur_widths = [len(cur) for cur in text_data]
            if with_headers:
                cur_widths.append(len(to_text(headers[idx], maxlen=maxlen)))
            widths.append(max(cur_widths))
        if with_headers:
            lines.append(' | '.join([h.center(widths[i])
                                     for i, h in enumerate(headers)]))
            lines.append('-+-'.join(['-' * w for w in widths]))
        for row in text_data:
            lines.append(' | '.join([cell.center(widths[j])
                                     for j, cell in enumerate(row)]))
        return '\n'.join(lines)
