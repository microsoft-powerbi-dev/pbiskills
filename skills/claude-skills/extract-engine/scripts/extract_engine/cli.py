"""
cli.py - the extract engine's command-line interface, via Click per the
environment spec in docs/extract-engine-mvp-prompt-v2-polars.md.

`run`, `benchmark`, `deploy-views`, and `load-config`'s write path are
CLI-only, never exposed as MCP tools - a production extract or a DDL
deployment must never be one agent decision away from firing. Only the
read-only, design-time pieces (dry-run, explain, validate-config,
devin-context) are also reachable through
extract_engine_authoring_mcp.py.

    extract benchmark --baseline --dataset <name>
    extract benchmark --feed <name> --compare-modes
    extract run          --feed <name> [--mode polars|sql] [--anchor-date ...]
                          [--window-years N] [--resume --run-id N]
    extract dry-run       --feed <name>
    extract explain       --feed <name> --dataset <name> --mode sql [--out <path>]
    extract deploy-views  --feed <name> [--i-understand-this-writes-to-the-database]
    extract load-config   --workbook <path> --feed <name> [--dry-run] [--allow-config-write]
    extract validate-config --feed <name>
"""
from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extract_engine import checkpoint, config_loader as cl, connection as conn_mod
from extract_engine import execution_polars as ep
from extract_engine import execution_sql as es
from extract_engine import guardrails, metadata


@click.group()
def cli():
    """Metadata-driven SQL Server extract engine."""


@cli.command("load-config")
@click.option("--workbook", required=True, type=click.Path(exists=True))
@click.option("--feed", "feed_name", required=True)
@click.option("--dry-run", is_flag=True)
@click.option("--allow-config-write", is_flag=True)
def load_config_cmd(workbook, feed_name, dry_run, allow_config_write):
    """Parse, validate, and (with --allow-config-write) upsert an Excel workbook."""
    config = cl.load_workbook(workbook)
    if config.feed.get("feed_name") != feed_name:
        raise click.ClickException(
            "Workbook's Feed sheet says {!r}, not {!r}.".format(config.feed.get("feed_name"), feed_name)
        )

    def _check_lookup(schema, table):
        with conn_mod.read_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT OBJECT_ID(?)", "{}.{}".format(schema, table))
                return cursor.fetchone()[0] is not None
            finally:
                cursor.close()

    report = cl.validate(config, check_lookup_tables_exist=_check_lookup)
    if not report.ok:
        click.echo(str(report), err=True)
        raise click.ClickException("Validation failed with {} issue(s).".format(len(report.issues)))
    click.echo("Validation passed.")

    if dry_run:
        click.echo("--dry-run: no database write performed.")
        return
    if not allow_config_write:
        raise click.ClickException("Pass --allow-config-write to actually upsert into meta.*.")

    import getpass

    with conn_mod.write_connection() as conn:
        version_id = cl.upsert(config, conn, loaded_by=getpass.getuser())
    click.echo("Loaded feed_config_version_id={}".format(version_id))


@cli.command("validate-config")
@click.option("--feed", "feed_name", required=True)
def validate_config_cmd(feed_name):
    """Re-validate the already-loaded meta.* rows for a feed. No Excel needed."""
    with conn_mod.read_connection() as conn:
        feed = metadata.get_feed(conn, feed_name)
        if feed is None:
            raise click.ClickException("No feed named {!r}.".format(feed_name))
        datasets = metadata.list_datasets(conn, feed.feed_id)
        problems = []
        for dataset in datasets:
            if dataset.dataset_mode == "primary" and not dataset.window_date_column:
                problems.append("{}: primary mode needs window_date_column".format(dataset.dataset_name))
            if dataset.dataset_mode == "dependent" and not dataset.dependency_target_column:
                problems.append("{}: dependent mode needs dependency_target_column".format(dataset.dataset_name))
    if problems:
        for problem in problems:
            click.echo(problem, err=True)
        raise click.ClickException("{} issue(s) found.".format(len(problems)))
    click.echo("Feed {!r}: {} dataset(s), no issues found.".format(feed_name, len(datasets)))


@cli.command("dry-run")
@click.option("--feed", "feed_name", required=True)
def dry_run_cmd(feed_name):
    """Show what a run would do: datasets, modes, and the SQL each would use."""
    with conn_mod.read_connection() as conn:
        feed = metadata.get_feed(conn, feed_name)
        if feed is None:
            raise click.ClickException("No feed named {!r}.".format(feed_name))
        datasets = metadata.list_datasets(conn, feed.feed_id)
        anchor_date = feed.anchor_date_default or datetime.date.today().isoformat()
        anchor_date = datetime.date.fromisoformat(anchor_date) if isinstance(anchor_date, str) else anchor_date
        for dataset in sorted(datasets, key=lambda d: d.run_ordinal):
            field_maps = metadata.list_field_maps(conn, dataset.dataset_id)
            lookups = metadata.list_lookups(conn, dataset.dataset_id)
            click.echo("\n=== {} ({}) ===".format(dataset.dataset_name, dataset.dataset_mode))
            if feed.default_execution_mode == "sql":
                click.echo(es.explain(dataset, field_maps, lookups, feed))
            else:
                from extract_engine.query_builder import build_select

                try:
                    plan = build_select(
                        dataset, field_maps, lookups,
                        anchor_date=anchor_date, window_years=feed.window_years_default, run_id=0,
                    )
                    click.echo(plan.sql)
                except ValueError as exc:
                    click.echo("(cannot preview: {})".format(exc))


@cli.command("explain")
@click.option("--feed", "feed_name", required=True)
@click.option("--dataset", "dataset_name", required=True)
@click.option("--mode", type=click.Choice(["sql"]), default="sql")
@click.option("--out", "out_path", default=None)
def explain_cmd(feed_name, dataset_name, mode, out_path):
    """Print (or save) the generated view DDL for one dataset - what you show a DBA."""
    with conn_mod.read_connection() as conn:
        feed = metadata.get_feed(conn, feed_name)
        if feed is None:
            raise click.ClickException("No feed named {!r}.".format(feed_name))
        dataset = metadata.get_dataset_by_name(conn, feed.feed_id, dataset_name)
        if dataset is None:
            raise click.ClickException("No dataset named {!r} in feed {!r}.".format(dataset_name, feed_name))
        all_datasets = metadata.list_datasets(conn, feed.feed_id)
        field_maps = metadata.list_field_maps(conn, dataset.dataset_id)
        lookups = metadata.list_lookups(conn, dataset.dataset_id)
        # all_datasets is passed so the preview matches exactly what
        # deploy-views actually deploys, including any internal
        # __key_source_ column a dependent dataset elsewhere in the feed needs.
        ddl = es.explain(dataset, field_maps, lookups, feed, all_datasets=all_datasets)

    if out_path:
        target = guardrails.resolve_artifact_path(out_path, "explain-{}.sql".format(dataset_name))
        target.write_text(ddl, encoding="utf-8")
        click.echo("Wrote {}".format(target))
    else:
        click.echo(ddl)


@cli.command("deploy-views")
@click.option("--feed", "feed_name", required=True)
@click.option("--i-understand-this-writes-to-the-database", "confirmed", is_flag=True)
def deploy_views_cmd(feed_name, confirmed):
    """Deploy CREATE OR ALTER VIEW for every dataset in a feed. Never implicit."""
    with conn_mod.read_connection() as ro_conn:
        feed = metadata.get_feed(ro_conn, feed_name)
        if feed is None:
            raise click.ClickException("No feed named {!r}.".format(feed_name))
        datasets = metadata.list_datasets(ro_conn, feed.feed_id)
        field_maps_by_dataset = {d.dataset_id: metadata.list_field_maps(ro_conn, d.dataset_id) for d in datasets}
        lookups_by_dataset = {d.dataset_id: metadata.list_lookups(ro_conn, d.dataset_id) for d in datasets}

    with conn_mod.write_connection() as w_conn:
        results = es.deploy_views(
            w_conn, feed, datasets, field_maps_by_dataset, lookups_by_dataset, confirmed=confirmed
        )
    for result in results:
        status = "DEPLOYED" if result.deployed else "SKIPPED ({})".format(result.blocked_reason)
        click.echo("{}: {}".format(result.view_name, status))
    if not confirmed:
        click.echo("\nPass --i-understand-this-writes-to-the-database to actually deploy.")


@cli.command("run")
@click.option("--feed", "feed_name", required=True)
@click.option("--mode", type=click.Choice(["polars", "sql"]), default=None)
@click.option("--anchor-date", default=None)
@click.option("--window-years", type=int, default=None)
@click.option("--resume", is_flag=True)
@click.option("--run-id", type=int, default=None)
def run_cmd(feed_name, mode, anchor_date, window_years, resume, run_id):
    """Run a feed: read, transform, sanitize, write, checkpoint."""
    import getpass
    import psutil

    with conn_mod.read_connection() as ro_conn:
        feed = metadata.get_feed(ro_conn, feed_name)
        if feed is None:
            raise click.ClickException("No feed named {!r}.".format(feed_name))
        datasets = metadata.list_datasets(ro_conn, feed.feed_id)
        cursor = ro_conn.cursor()
        try:
            cursor.execute(
                "SELECT feed_config_version_id FROM meta.feed_config_version "
                "WHERE feed_id = ? AND is_current = 1", feed.feed_id,
            )
            version_row = cursor.fetchone()
        finally:
            cursor.close()
    if version_row is None:
        raise click.ClickException("No current feed_config_version for {!r}. Run load-config first.".format(feed_name))
    feed_config_version_id = int(version_row[0])

    effective_mode = mode or feed.default_execution_mode
    effective_anchor = (
        datetime.date.fromisoformat(anchor_date) if anchor_date
        else (feed.anchor_date_default and datetime.date.fromisoformat(feed.anchor_date_default))
        or datetime.date.today()
    )
    effective_window = window_years if window_years is not None else feed.window_years_default

    with conn_mod.write_connection() as w_conn:
        if checkpoint.is_concurrent_run_active(w_conn, feed.feed_id):
            raise click.ClickException(
                "A run is already in progress for feed {!r}. Refusing to start a second one.".format(feed_name)
            )
        if resume and run_id:
            active_run_id = run_id
        else:
            active_run_id = checkpoint.start_run(
                w_conn, feed.feed_id, feed_config_version_id, effective_anchor, effective_window,
                effective_mode, getpass.getuser(),
            )

    with conn_mod.read_connection() as ro_conn:
        statuses = checkpoint.get_run_detail_statuses(ro_conn, active_run_id) if resume else {}
    remaining = checkpoint.datasets_to_process(datasets, statuses, resume=resume)

    started = time.monotonic()
    peak_rss = psutil.Process().memory_info().rss
    output_dir = Path(feed.output_root)

    for dataset in sorted(remaining, key=lambda d: d.run_ordinal):
        with conn_mod.write_connection() as w_conn:
            checkpoint.mark_dataset_running(w_conn, active_run_id, dataset.dataset_id)
        try:
            with conn_mod.read_connection() as ro_conn:
                field_maps = metadata.list_field_maps(ro_conn, dataset.dataset_id)
                lookups = metadata.list_lookups(ro_conn, dataset.dataset_id)
                writer = ep.PartWriter(
                    output_dir=output_dir, file_stem=dataset.output_file_stem, delimiter=feed.delimiter,
                    line_ending=b"\r\n" if feed.line_ending == "CRLF" else b"\n",
                    null_sentinel=feed.null_sentinel, max_rows_per_file=feed.max_rows_per_file,
                    emit_header_row=feed.emit_header_row, emit_trailer_row=feed.emit_trailer_row,
                )
                key_acc, key_raw_column = _key_accumulator_for(dataset, datasets, effective_mode)
                target_column_order = [fm.target_column for fm in sorted(field_maps, key=lambda f: f.ordinal)]
                if feed.emit_concat_ws_line:
                    target_column_order = ["Line"]
                offsets = {}
                total_rows = 0
                for batch in _read_dataset_batches(ro_conn, dataset, field_maps, lookups, feed,
                                                     effective_mode, effective_anchor, effective_window,
                                                     active_run_id, key_raw_column):
                    if key_acc is not None:
                        key_acc.add_batch(batch)
                    if effective_mode != "sql":
                        batch = ep.apply_transform(batch, field_maps)
                        batch = ep.add_row_sequence_columns(batch, field_maps, offsets)
                        batch = ep.reorder_columns(batch, field_maps)
                        batch = ep.apply_sanitization(batch, feed)
                    else:
                        # Drop any internal __key_source_ columns the view
                        # exposed for key staging: they were captured above by
                        # key_acc, but must never reach the output file itself.
                        batch = batch.select(target_column_order)
                    writer.write_batch(batch)
                    total_rows += batch.height
                result = writer.finish()

            if key_acc is not None:
                keys = key_acc.finish()
                with conn_mod.write_connection() as w_conn:
                    checkpoint.stage_dataset_keys(w_conn, active_run_id, dataset.dataset_id, keys)

            with conn_mod.write_connection() as w_conn:
                checkpoint.mark_dataset_complete(
                    w_conn, active_run_id, dataset.dataset_id,
                    row_count=result.row_count, part_count=result.part_count, checksum=result.checksum,
                )
            click.echo("{}: {} rows, {} part(s)".format(dataset.dataset_name, result.row_count, result.part_count))
        except Exception:
            with conn_mod.write_connection() as w_conn:
                checkpoint.mark_dataset_failed(w_conn, active_run_id, dataset.dataset_id)
            raise
        peak_rss = max(peak_rss, psutil.Process().memory_info().rss)

    duration_ms = int((time.monotonic() - started) * 1000)
    with conn_mod.write_connection() as w_conn:
        checkpoint.complete_run(w_conn, active_run_id, duration_ms=duration_ms, peak_rss_bytes=peak_rss)
    click.echo("Run {} complete in {} ms.".format(active_run_id, duration_ms))


def _key_accumulator_for(dataset, all_datasets, mode):
    """Returns (KeyAccumulator, raw_column_name) or (None, None).

    ``raw_column_name`` is what execution_sql.key_source_alias() needs to ask
    the view to additionally expose in SQL mode - kept alongside the
    accumulator rather than re-derived from its (possibly aliased) key_column,
    which would mean decoding a string prefix back apart.
    """
    for other in all_datasets:
        if other.dataset_mode == "dependent" and other.depends_on_dataset_id == dataset.dataset_id:
            raw_column = other.dependency_source_column
            # SQL mode reads the raw key column back under its reserved
            # __key_source_ alias (see execution_sql.key_source_alias): the
            # view's real output is target-named, so the raw column is not
            # otherwise guaranteed to be present under its own name.
            column = es.key_source_alias(raw_column) if mode == "sql" else raw_column
            return ep.KeyAccumulator(column), raw_column
    return None, None


def _read_dataset_batches(conn, dataset, field_maps, lookups, feed, mode, anchor_date, window_years, run_id, key_raw_column=None):
    if mode == "sql":
        extra_key_columns = [key_raw_column] if key_raw_column else None
        plan = es.build_view_read_query(
            dataset, field_maps, feed, anchor_date=anchor_date, window_years=window_years, run_id=run_id,
            extra_key_columns=extra_key_columns,
        )
        import polars as pl

        return pl.read_database(plan.sql, conn, iter_batches=True, batch_size=100_000,
                                 execute_options={"parameters": plan.params} if plan.params else None)
    from extract_engine.query_builder import build_select

    plan = build_select(dataset, field_maps, lookups, anchor_date=anchor_date, window_years=window_years, run_id=run_id)
    return ep.read_batches(conn, plan)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
