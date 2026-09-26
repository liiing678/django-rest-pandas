"""
分层转换管道（rest_pandas.pipeline）的类型与行为契约测试。

- PipelineLayerTestCase：入口解析 / 数据装载 / 输出渲染三层的单元契约；
- SerializerPipelineTestCase：serializer 适配层的集成契约（不依赖 DB）；
- OutputContractTestCase：同一接口在 CSV / JSON 出口的字段名与类型
  一致性契约（锁定现有输出结构，防止改一处影响另一处）。
"""

import json
import unittest

from pandas import DataFrame
from itertable import load_string
from rest_framework.test import APITestCase

from rest_pandas.pipeline import (
    parse_input,
    load_dataframe,
    render_dataframe,
)
from rest_pandas.serializers import PandasSerializer, SimpleSerializer
from tests.testapp.models import TimeSeries, CustomIndexSeries


class PipelineLayerTestCase(unittest.TestCase):
    """
    三层转换各自的输入输出契约。
    """

    records = [
        {"id": 1, "date": "2014-01-01", "value": 0.5},
        {"id": 2, "date": "2014-01-02", "value": 0.4},
    ]

    # 入口解析 parse_input

    def test_parse_input_dataframe_passthrough(self):
        df = DataFrame(self.records)
        self.assertIs(parse_input(df), df)

    def test_parse_input_records_default(self):
        result = parse_input(self.records)
        self.assertEqual(result, self.records)

    def test_parse_input_applies_to_record(self):
        result = parse_input([1, 2], lambda n: {"n": n})
        self.assertEqual(result, [{"n": 1}, {"n": 2}])

    def test_parse_input_accepts_iterable(self):
        result = parse_input(iter(self.records))
        self.assertEqual(result, self.records)

    # 数据装载 load_dataframe

    def test_load_dataframe_columns_and_row_index(self):
        df = load_dataframe(self.records)
        self.assertEqual(list(df.columns), ["id", "date", "value"])
        self.assertEqual(df.index.name, "row")

    def test_load_dataframe_field_labels(self):
        df = load_dataframe(
            self.records, field_labels={"value": "Measured Value"}
        )
        self.assertEqual(
            list(df.columns), ["id", "date", "Measured Value"]
        )

    def test_load_dataframe_index_list(self):
        df = load_dataframe(self.records, index=["id"])
        self.assertEqual(list(df.index), [1, 2])
        self.assertEqual(df.index.name, "id")
        self.assertEqual(list(df.columns), ["date", "value"])

    def test_load_dataframe_index_callable_gets_labeled_df(self):
        seen = {}

        def get_index(dataframe):
            seen["columns"] = list(dataframe.columns)
            return ["id"]

        df = load_dataframe(
            self.records,
            field_labels={"value": "Measured Value"},
            index=get_index,
        )
        self.assertEqual(
            seen["columns"], ["id", "date", "Measured Value"]
        )
        self.assertEqual(df.index.name, "id")

    def test_load_dataframe_index_none_value(self):
        df = load_dataframe(
            [{"g": "a", "v": 1}, {"g": None, "v": 2}],
            index=["g"],
            index_none_value="-",
        )
        self.assertEqual(list(df.index), ["a", "-"])

    def test_load_dataframe_does_not_mutate_input(self):
        source = DataFrame(self.records)
        df = load_dataframe(source, index=["id"])
        self.assertEqual(list(source.columns), ["id", "date", "value"])
        self.assertEqual(list(df.columns), ["date", "value"])

    # 输出渲染 render_dataframe

    def test_render_dataframe_without_view(self):
        df = DataFrame(self.records)
        self.assertIs(render_dataframe(df), df)

    def test_render_dataframe_view_without_hook(self):
        df = DataFrame(self.records)
        self.assertIs(render_dataframe(df, object()), df)

    def test_render_dataframe_applies_view_hook(self):
        class View:
            def transform_dataframe(self, dataframe):
                return dataframe.assign(total=dataframe["value"] * 2)

        df = render_dataframe(DataFrame(self.records), View())
        self.assertEqual(
            list(df.columns), ["id", "date", "value", "total"]
        )


class SerializerPipelineTestCase(unittest.TestCase):
    """
    serializer -> pipeline 的适配契约（与 NoModelView 同路径）。
    """

    data = [
        {"x": 5, "y": 7},
        {"x": 3, "y": 2},
    ]

    def get_dataframe(self, **kwargs):
        serializer = PandasSerializer(
            self.data, child=SimpleSerializer(), **kwargs
        )
        return serializer.data

    def test_records_become_dataframe(self):
        df = self.get_dataframe()
        self.assertIsInstance(df, DataFrame)
        self.assertEqual(list(df.columns), ["x", "y"])
        self.assertEqual(df.index.name, "row")

    def test_dataframe_input_passthrough(self):
        serializer = PandasSerializer(
            DataFrame(self.data), child=SimpleSerializer()
        )
        df = serializer.data
        self.assertEqual(list(df.columns), ["x", "y"])
        self.assertEqual(df.index.name, "row")

    def test_empty_input_gives_empty_dataframe(self):
        serializer = PandasSerializer([], child=SimpleSerializer())
        df = serializer.data
        self.assertIsInstance(df, DataFrame)
        self.assertEqual(len(df), 0)

    def test_view_transform_hook_via_context(self):
        class View:
            def transform_dataframe(self, dataframe):
                return dataframe.assign(z=dataframe["x"] + dataframe["y"])

        df = self.get_dataframe(context={"view": View()})
        self.assertEqual(list(df.columns), ["x", "y", "z"])
        self.assertEqual(list(df["z"]), [12, 5])


class OutputContractTestCase(APITestCase):
    """
    跨出口一致性契约：同一接口的 CSV / JSON 输出必须使用相同的
    字段名与等价的字段值（结构锁定，防止改一处影响另一处）。
    """

    maxDiff = None

    def setUp(self):
        data = (
            ("2014-01-01", 0.5),
            ("2014-01-02", 0.4),
        )
        for date, value in data:
            TimeSeries.objects.create(date=date, value=value)
            CustomIndexSeries.objects.create(
                code="v" + date.replace("-", ""),
                value=value,
            )

    def get_csv(self, url):
        response = self.client.get(url)
        return load_string(response.content.decode("utf-8"))

    def get_json(self, url):
        response = self.client.get(url)
        return json.loads(response.content.decode("utf-8"))

    def assert_same_fields(self, csv_data, json_data, fields):
        self.assertEqual(list(csv_data.field_map.keys()), fields)
        self.assertEqual(list(json_data[0].keys()), fields)

    def test_timeseries_field_names_consistent(self):
        csv_data = self.get_csv("/timeseries.csv")
        json_data = self.get_json("/timeseries.json")
        self.assert_same_fields(csv_data, json_data, ["id", "date", "value"])

    def test_timeseries_field_labels_consistent(self):
        csv_data = self.get_csv("/timeserieslabels.csv")
        json_data = self.get_json("/timeserieslabels.json")
        self.assert_same_fields(
            csv_data, json_data, ["id", "Event Date", "Measured Value"]
        )

    def test_custom_index_field_names_consistent(self):
        csv_data = self.get_csv("/customindex.csv")
        json_data = self.get_json("/customindex.json")
        self.assert_same_fields(csv_data, json_data, ["code", "value"])

    def test_simple_view_field_names_consistent(self):
        csv_data = self.get_csv("/nomodel.csv")
        json_data = self.get_json("/nomodel.json")
        self.assert_same_fields(csv_data, json_data, ["x", "y"])

    def test_field_values_equivalent_across_exits(self):
        csv_row = self.get_csv("/timeseries.csv")[0]
        json_row = self.get_json("/timeseries.json")[0]

        # 各出口类型契约锁定：JSON 为原生类型，CSV 为字符串
        self.assertIsInstance(json_row["id"], int)
        self.assertIsInstance(json_row["value"], float)
        self.assertEqual(json_row["date"], "2014-01-01T00:00:00.000")
        self.assertEqual(csv_row.date, "2014-01-01")

        # 跨出口取值等价
        self.assertEqual(int(csv_row.id), json_row["id"])
        self.assertEqual(float(csv_row.value), json_row["value"])
