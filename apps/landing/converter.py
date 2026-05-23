from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import duckdb


class ConversionStrategy(ABC):
    @abstractmethod
    def convert(self, source_path: str, dest_path: str) -> None:
        ...


class CsvConversionStrategy(ConversionStrategy):
    def convert(self, source_path: str, dest_path: str) -> None:

        duckdb.sql(
            f"COPY (SELECT * FROM read_csv_auto('{source_path}')) TO '{dest_path}' (FORMAT PARQUET)"
        )


class HtmlConversionStrategy(ConversionStrategy):
    def convert(self, source_path: str, dest_path: str) -> None:

        tables = pd.read_html(source_path)
        df = tables[0]
        df.to_parquet(dest_path, index=False)


class ConversionContext:
    def __init__(self, strategy: ConversionStrategy) -> None:
        self._strategy = strategy

    def execute(self, source_path: str, dest_path: str) -> None:
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        self._strategy.convert(source_path, dest_path)
