"""
Layered conversion pipeline (分层转换管道).

接口参数、查询集、DataFrame 之间的转换统一收敛到本模块，DataFrame
作为各层之间唯一的中间表示 (intermediate representation)。三个层次：

1. 入口解析 ``parse_input``
   视图给出的原始数据 (DataFrame、字典列表、查询集等可迭代记录)
   归一化为 ``Records`` 或原样透传的 ``DataFrame``。

2. 数据装载 ``load_dataframe``
   把 ``Records`` / ``DataFrame`` 装载为契约明确的 ``DataFrame``：
   应用字段标签 (field labels)、设置索引 (index)、填充索引空值。

3. 输出渲染 ``render_dataframe``
   对 ``DataFrame`` 应用视图级 ``transform_dataframe`` 钩子，
   结果交给 renderer 输出为 CSV / JSON / Excel 等格式。

每一层的输入输出类型见下方类型别名与各函数签名；行为契约由
tests/test_pipeline.py 锁定。序列化器 (serializers.py) 只负责把
serializer 状态 (Meta 选项、context) 适配到这些纯函数上，不再各自
实现转换逻辑。
"""

from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Union,
)

from pandas import DataFrame


# 一条记录：字段名 -> 序列化后的值
Record = Dict[str, Any]

# 记录集：入口解析与数据装载之间的标准记录形式
Records = List[Record]

# 入口解析的输出：DataFrame 原样透传，否则为 Records
ParsedInput = Union[DataFrame, Records]

# 单条记录的解析器（通常是 serializer 的 child.to_representation）
RecordParser = Callable[[Any], Record]

# 索引契约：字段名列表，或从（已应用字段标签的）DataFrame 推导索引的函数
IndexSpec = Union[Sequence[str], Callable[[DataFrame], Sequence[str]]]


def parse_input(
    data: Union[DataFrame, Iterable[Any]],
    to_record: Optional[RecordParser] = None,
) -> ParsedInput:
    """
    入口解析：把视图/查询集给出的原始数据归一化。

    - ``DataFrame`` 输入原样透传（已是中间表示，不再逐条解析）；
    - 其他可迭代输入（查询集、模型列表、字典列表等）逐条经
      ``to_record`` 解析为 ``Record``，返回 ``Records``；
    - ``to_record`` 缺省时按原样返回每条记录（适用于已是 dict 的
      简单视图数据）。

    输出契约：``ParsedInput``，即 ``DataFrame | List[Record]``。
    """
    if isinstance(data, DataFrame):
        return data
    if to_record is None:
        return list(data)
    return [to_record(item) for item in data]


def load_dataframe(
    data: Union[DataFrame, Records],
    field_labels: Optional[Dict[str, str]] = None,
    index: Optional[IndexSpec] = None,
    index_none_value: Any = None,
) -> DataFrame:
    """
    数据装载：把入口解析的结果装载为契约明确的 DataFrame。

    - 输入 ``data`` 总是先复制（``DataFrame(data)``），调用方传入的
      DataFrame 不会被原地修改；
    - ``field_labels`` 非空时按 ``{字段名: 标签}`` 重命名列；
    - ``index`` 为字段名列表，或接收（重命名后的）DataFrame 并返回
      字段名列表的函数；非空时设为索引，空索引则把自动索引列命名为
      ``"row"`` 以保证 CSV 输出合法；
    - ``index_none_value`` 非 None 时先填充索引列中的空值
      （无法填充的列静默跳过）。

    输出契约：带索引（或 ``"row"`` 命名索引）的新 ``DataFrame``。
    """
    dataframe = DataFrame(data)
    if field_labels:
        dataframe.rename(columns=field_labels, inplace=True)
    if callable(index):
        index = index(dataframe)
    if index:
        if index_none_value is not None:
            for key in index:
                try:
                    dataframe[key].fillna(index_none_value, inplace=True)
                except ValueError:
                    pass
        dataframe.set_index(index, inplace=True)
    else:
        # Name auto-index column to ensure valid CSV output
        dataframe.index.name = "row"
    return dataframe


def render_dataframe(
    dataframe: DataFrame,
    view: Optional[Any] = None,
) -> DataFrame:
    """
    输出渲染（serializer 侧）：应用视图级转换钩子。

    - ``view`` 定义了 ``transform_dataframe`` 时，把 DataFrame 交给
      视图做最后的定制转换；
    - 否则原样返回，交由 renderer 输出。

    输出契约：待渲染的 ``DataFrame``（行列结构即最终输出结构）。
    """
    if view and hasattr(view, "transform_dataframe"):
        return view.transform_dataframe(dataframe)
    return dataframe
