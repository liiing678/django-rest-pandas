"""
Explicit intermediate representation for the conversions between
request parameters, serialized records, and pandas DataFrames.

The pipeline is split into three layers, each with a single owner:

1. Entry parsing (:func:`parse_request_options`)
   HTTP request query parameters -> :class:`RequestOptions`.
   Views, serializers, and renderers must not read ``request.GET``
   directly; they consume the typed options object instead.

2. Data loading (:class:`FrameSpec` + :func:`load_dataframe`)
   Serialized records (a list of dicts, or an existing DataFrame)
   -> an indexed, labeled DataFrame, as described by the spec.
   Serializers translate their ``Meta`` options into a ``FrameSpec``;
   this module applies it.  Column types are whatever the serializer
   produced (dates stay dates, floats stay floats); the only
   normalization applied here is renaming columns to their labels and
   filling missing index values with ``index_none_value``.

3. Output rendering (see rest_pandas.renderers)
   DataFrame + :class:`RequestOptions` -> CSV/JSON/HTML/...
   Renderers may reformat values for their output format but must not
   rename fields or alter the frame structure, so every export of the
   same endpoint exposes the same field names.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from pandas import DataFrame


# A dataset as produced by DRF serializers: a list of row dicts, or a
# pre-built DataFrame (e.g. from django-pandas or PandasSimpleView).
Records = Union[Sequence[Mapping[str, Any]], DataFrame]


@dataclass(frozen=True)
class RequestOptions:
    """
    DRP-related query parameters, parsed once at the entry layer.

    All values are raw strings as provided by the client (or None when
    absent); validating them against per-renderer choices is the
    renderer's responsibility.
    """

    group: Optional[str] = None
    orient: Optional[str] = None
    date_format: Optional[str] = None


def parse_request_options(request) -> RequestOptions:
    """
    Entry parsing layer: extract the query parameters DRP understands
    from a DRF request into a typed :class:`RequestOptions`.
    """
    if request is None:
        return RequestOptions()
    get = request.GET.get
    return RequestOptions(
        group=get("group") or None,
        orient=get("orient") or None,
        date_format=get("date_format") or None,
    )


@dataclass(frozen=True)
class FrameSpec:
    """
    Type contract for the data-loading layer.

    :param index_fields: Output column names (after labels are applied)
        to use as the DataFrame index.  Empty means a default integer
        index named ``"row"``.
    :param field_labels: Mapping of serializer field name -> output
        column label.
    :param apply_field_labels: Whether to rename columns via
        ``field_labels``.
    :param index_none_value: Fill value for missing (None/NaN) index
        entries, e.g. ``"-"``.  None disables filling.
    """

    index_fields: Tuple[str, ...] = ()
    field_labels: Dict[str, str] = field(default_factory=dict)
    apply_field_labels: bool = True
    index_none_value: Optional[str] = None


def load_dataframe(data: Records, spec: FrameSpec) -> DataFrame:
    """
    Data-loading layer: build an indexed, labeled DataFrame from
    serialized records according to ``spec``.

    The same spec must be used for every export of a dataset, so field
    names and index structure are identical across output formats.
    """
    dataframe = DataFrame(data)
    if spec.apply_field_labels and spec.field_labels:
        dataframe.rename(columns=spec.field_labels, inplace=True)
    if spec.index_fields:
        if spec.index_none_value is not None:
            for key in spec.index_fields:
                try:
                    dataframe[key] = dataframe[key].fillna(
                        spec.index_none_value
                    )
                except (ValueError, TypeError):
                    pass
        dataframe.set_index(list(spec.index_fields), inplace=True)
    else:
        # Name auto-index column to ensure valid CSV output
        dataframe.index.name = "row"
    return dataframe
