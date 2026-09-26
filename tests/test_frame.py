"""
Tests for the intermediate representation layer (rest_pandas.frame):
entry parsing, data loading, and the cross-format field contract.
"""

import csv
import io
import json
import unittest

from django.test import RequestFactory
from pandas import DataFrame
from rest_framework.test import APITestCase

from rest_pandas import (
    FrameSpec,
    RequestOptions,
    load_dataframe,
    parse_request_options,
)
from rest_pandas import PandasSerializer, PandasUnstackedSerializer
from tests.testapp.models import TimeSeries
from tests.testapp.serializers import (
    MultiTimeSeriesSerializer,
    TimeSeriesLabelsSerializer,
)


class RequestOptionsTestCase(unittest.TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_defaults_without_request(self):
        self.assertEqual(parse_request_options(None), RequestOptions())

    def test_defaults_without_params(self):
        request = self.factory.get("/data")
        self.assertEqual(parse_request_options(request), RequestOptions())

    def test_parses_known_params(self):
        request = self.factory.get(
            "/data?group=year&orient=split&date_format=epoch&other=1"
        )
        options = parse_request_options(request)
        self.assertEqual(options.group, "year")
        self.assertEqual(options.orient, "split")
        self.assertEqual(options.date_format, "epoch")

    def test_empty_params_become_none(self):
        request = self.factory.get("/data?group=&orient=")
        options = parse_request_options(request)
        self.assertIsNone(options.group)
        self.assertIsNone(options.orient)


class LoadDataFrameTestCase(unittest.TestCase):
    records = [
        {"id": 1, "date": "2014-01-01", "value": 0.5},
        {"id": 2, "date": "2014-01-02", "value": 0.4},
    ]

    def test_default_spec_names_auto_index(self):
        dataframe = load_dataframe(self.records, FrameSpec())
        self.assertEqual(dataframe.index.name, "row")
        self.assertEqual(list(dataframe.columns), ["id", "date", "value"])

    def test_index_fields_become_index(self):
        spec = FrameSpec(index_fields=("id",))
        dataframe = load_dataframe(self.records, spec)
        self.assertEqual(list(dataframe.index), [1, 2])
        self.assertEqual(list(dataframe.columns), ["date", "value"])

    def test_field_labels_rename_columns(self):
        spec = FrameSpec(
            index_fields=("id",),
            field_labels={"id": "id", "date": "Event Date"},
        )
        dataframe = load_dataframe(self.records, spec)
        self.assertEqual(dataframe.index.name, "id")
        self.assertEqual(list(dataframe.columns), ["Event Date", "value"])

    def test_field_labels_ignored_when_disabled(self):
        spec = FrameSpec(
            field_labels={"date": "Event Date"},
            apply_field_labels=False,
        )
        dataframe = load_dataframe(self.records, spec)
        self.assertIn("date", dataframe.columns)

    def test_index_none_value_fills_missing_index(self):
        records = [
            {"date": "2014-01-01", "series": "a", "value": 0.5},
            {"date": "2014-01-02", "series": None, "value": 0.4},
        ]
        spec = FrameSpec(
            index_fields=("date", "series"),
            index_none_value="-",
        )
        dataframe = load_dataframe(records, spec)
        series = dataframe.index.get_level_values("series")
        self.assertEqual(list(series), ["a", "-"])

    def test_dataframe_input_is_accepted(self):
        spec = FrameSpec(index_fields=("id",))
        dataframe = load_dataframe(DataFrame(self.records), spec)
        self.assertEqual(list(dataframe.index), [1, 2])


class SerializerSpecTestCase(unittest.TestCase):
    """
    Serializers translate Meta options into a FrameSpec; the spec is
    the single source of truth for the data-loading layer.
    """

    def test_base_serializer_spec(self):
        serializer = PandasSerializer(child=TimeSeriesLabelsSerializer())
        spec = serializer.get_frame_spec(serializer.get_index(None))
        self.assertEqual(spec.index_fields, ("id",))
        self.assertEqual(
            spec.field_labels,
            {"id": "id", "date": "Event Date", "value": "Measured Value"},
        )
        self.assertTrue(spec.apply_field_labels)
        self.assertIsNone(spec.index_none_value)

    def test_unstacked_serializer_spec(self):
        serializer = PandasUnstackedSerializer(
            child=MultiTimeSeriesSerializer()
        )
        spec = serializer.get_frame_spec(serializer.get_index(None))
        self.assertEqual(spec.index_fields, ("date", "series"))
        self.assertEqual(spec.index_none_value, "-")


class CrossFormatContractTestCase(APITestCase):
    """
    Every export of the same endpoint must expose the same field names;
    only the value formatting may differ between renderers.
    """

    def setUp(self):
        TimeSeries.objects.create(date="2014-01-01", value=0.5)
        TimeSeries.objects.create(date="2014-01-02", value=0.4)

    def csv_fields(self, url):
        response = self.client.get(url)
        header = response.content.decode("utf-8").splitlines()[0]
        return next(csv.reader(io.StringIO(header)))

    def test_csv_txt_json_share_field_names(self):
        csv_fields = self.csv_fields("/timeseries.csv")
        txt_fields = self.csv_fields("/timeseries.txt")
        response = self.client.get("/timeseries.json")
        json_fields = list(json.loads(response.content.decode("utf-8"))[0])
        self.assertEqual(csv_fields, ["id", "date", "value"])
        self.assertEqual(txt_fields, csv_fields)
        self.assertEqual(json_fields, csv_fields)

    def test_labeled_fields_consistent_across_formats(self):
        csv_fields = self.csv_fields("/timeserieslabels.csv")
        response = self.client.get("/timeserieslabels.json")
        json_fields = list(json.loads(response.content.decode("utf-8"))[0])
        self.assertEqual(json_fields, csv_fields)
