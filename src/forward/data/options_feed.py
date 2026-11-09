"""Historical option bar feeds with an explicit, dated contract catalog."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import polars as pl

from forward.core.instrument_models import Instrument
from forward.core.option_catalog import OptionContractCatalog
from forward.data.csv_feed import CsvFeed


class OptionsCsvFeed(CsvFeed):
    """Load one imported OHLCV dataset and its matching option instruments.

    Args:
        bars_path: Long-format CSV containing ``ts,symbol,open,high,low,close``.
        instruments_path: JSON array of full Instrument records for that period.
            Every option record must include ``expiry_type`` as ``WEEKLY`` or
            ``MONTHLY``; missing classification is rejected.

    Raises:
        ValueError: If bars reference symbols absent from the contract file.
    """

    def __init__(self, bars_path: str | Path, instruments_path: str | Path) -> None:
        import json

        instrument_rows = json.loads(Path(instruments_path).read_text(encoding="utf-8"))
        instruments = {
            instrument.symbol: instrument
            for instrument in (Instrument.model_validate(row) for row in instrument_rows)
        }
        super().__init__(path=bars_path, instruments=instruments)
        self._frame = self.frame.with_columns(pl.col("symbol").str.to_uppercase())
        symbols = set(self.frame["symbol"].str.to_uppercase().unique().to_list())
        unknown = sorted(symbols - set(instruments))
        if unknown:
            raise ValueError(f"bar dataset has symbols missing from instruments.json: {unknown[:20]}")
        if not any(instrument.is_option for instrument in instruments.values()):
            raise ValueError("instrument catalog contains no option contracts")
        self.contract_catalog = OptionContractCatalog(list(instruments.values()))
        self._instruments = {symbol: instruments[symbol] for symbol in sorted(symbols)}


class OptionsCsvDatasetAdapter:
    """Open, validate, and store imported CSV options datasets.

    Args:
        repository_root: Root used to resolve configured relative folders.
    """

    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root

    def import_archive(
        self,
        archive: bytes,
        configured_directory: str,
        owner_id: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Save and validate the two files in an options data archive.

        Args:
            archive: ZIP bytes containing the options data.
            configured_directory: Configured base folder for imported datasets.
            owner_id: Account that owns the imported dataset.
            max_bytes: Maximum compressed and uncompressed archive size.

        Returns:
            The stored dataset ID, paths, folder, and row counts.

        Raises:
            ValueError: If the archive has the wrong files or invalid data.
        """
        if len(archive) > max_bytes:
            raise ValueError(f"dataset archive exceeds the {max_bytes}-byte limit")

        try:
            with zipfile.ZipFile(BytesIO(archive)) as zipped:
                files = [item for item in zipped.infolist() if not item.is_dir()]
                expected = {"bars.csv", "instruments.json"}
                if (
                    {item.filename for item in files} != expected
                    or len(files) != len(expected)
                    or any(Path(item.filename).name != item.filename for item in files)
                ):
                    raise ValueError(
                        "ZIP must contain exactly bars.csv and instruments.json at its root"
                    )
                if sum(item.file_size for item in files) > max_bytes:
                    raise ValueError("uncompressed dataset exceeds the configured size limit")
                bars = zipped.read("bars.csv")
                instruments = zipped.read("instruments.json")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ValueError(f"invalid options dataset archive: {exc}") from exc

        configured_path = Path(configured_directory)
        root = (
            configured_path
            if configured_path.is_absolute()
            else self._repository_root / configured_path
        ).resolve()
        destination = root / owner_id
        destination.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(dir=destination))
        try:
            bars_path = temporary / "bars.csv"
            instruments_path = temporary / "instruments.json"
            bars_path.write_bytes(bars)
            instruments_path.write_bytes(instruments)
            bar_count, symbol_count = self.inspect(bars_path, instruments_path)
            dataset_id = uuid.uuid4().hex
            folder = destination / dataset_id
            temporary.rename(folder)
        except Exception:
            shutil.rmtree(temporary)
            raise

        return {
            "dataset_id": dataset_id,
            "directory": str(folder),
            "bars_path": str(folder / "bars.csv"),
            "instruments_path": str(folder / "instruments.json"),
            "bar_count": bar_count,
            "symbol_count": symbol_count,
        }

    def remove_import(self, directory: str) -> None:
        """Remove a dataset folder after its metadata write fails.

        Args:
            directory: Folder created by ``import_archive``.
        """
        shutil.rmtree(Path(directory))

    def load(self, bars_path: str | Path, instruments_path: str | Path) -> OptionsCsvFeed:
        """Load one dataset from its bars and instrument files.

        Args:
            bars_path: Long-format OHLCV CSV path.
            instruments_path: Matching dated instrument catalog path.

        Returns:
            A validated feed for backtesting.
        """
        return OptionsCsvFeed(bars_path, instruments_path)

    def inspect(self, bars_path: str | Path, instruments_path: str | Path) -> tuple[int, int]:
        """Validate a dataset and report its size.

        Args:
            bars_path: Long-format OHLCV CSV path.
            instruments_path: Matching dated instrument catalog path.

        Returns:
            The number of bars and unique symbols.

        Raises:
            ValueError: If the dataset contains no valid bars.
        """
        feed = self.load(bars_path, instruments_path)
        bar_count = feed.frame.height
        if bar_count == 0:
            raise ValueError("bars.csv contains no valid rows")
        return bar_count, feed.frame["symbol"].n_unique()

    def fingerprint(self, bars_path: str | Path, instruments_path: str | Path) -> str:
        """Return a stable hash for the files that make up the dataset.

        Args:
            bars_path: Long-format OHLCV file.
            instruments_path: Matching instrument catalog file.

        Returns:
            SHA-256 fingerprint over both files in the given order.
        """
        digest = hashlib.sha256()
        for path in (bars_path, instruments_path):
            digest.update(Path(path).read_bytes())
        return digest.hexdigest()
