from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, catalogs, jobs
from .service import Service
from .storage import Dataset, dump


def create_server(config):
    service = Service(config)
    server = MCPServer(
        "fgis-mcp",
        version=__version__,
        instructions=(
            "FGIS MCP is an evidence-first factual source for official FGIS CS (Минстрой России) data. "
            "Evidence rule: no evidence from MCP -> no fact in answer (UNSUPPORTED_BY_FGIS_MCP). "
            "1. Search first: before claiming that a norm exists or proposing a norm, call fgis_search_norms (or fgis_batch_search_norms for multiple items). "
            "2. Read the specific norm: before claiming what a norm includes, its unit, work steps, resources, mass, or technical characteristics, call fgis_read_norm (or fgis_batch_read_norms for multiple items). It returns the self-contained norm card (hierarchy, work steps, resources, mass, separate editions, structured provenance). Do NOT call fgis_read_document merely to inspect norm resources or work steps. "
            "3. Read official documents: before any normative conclusion, technical part citation, or methodology rule, read the official text via fgis_read_document, fgis_search_document, fgis_document_outline or fgis_read_document_table. These tools provide surrounding context (technical parts, general provisions, table coefficients, annexes) and do NOT duplicate the norm card. "
            "4. Never reconstruct norm codes from model memory; never invent codes absent from MCP results. "
            "5. Never invent analogues; show analogues only if returned by MCP search, and label them as candidates. "
            "6. Never substitute model knowledge for missing search results; if unconfirmed, state 'не найдено' or 'нормативное основание не подтверждено'. "
            "7. Norm applicability is determined not only by title, but by work steps, resources, unit, collection/table, technical parts, and official documents. "
            "8. A coefficient cannot be considered applicable merely because a numerical value exists; conditions of application, surrounding text, and exceptions must be verified. "
            "9. Order date does not automatically equal document effective date. "
            "10. Absence in local dataset does NOT mean absence in FGIS CS; distinguish local dataset gaps from absence in FGIS. "
            "11. If evidence is insufficient, explicitly state the limitation (UNRESOLVED_CONDITION or UNSUPPORTED_BY_FGIS_MCP). "
            "12. For coefficients: fgis_extract_coefficients requires a known document_guid and/or table_index; it does not accept a text query argument. "
            "13. Region -> zone -> period IDs come from fgis_catalog. Downloads return durable job IDs; poll fgis_job_status. A complete job means requested tasks succeeded, not that the whole FSNB is complete."
        ),
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    local = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=read)
    def fgis_sources() -> dict:
        """Available catalogues: FSNB, FER, TER registry, methodologies/coefficients, prices, archives and limits."""
        return catalogs.inventory()

    @server.tool(annotations=read)
    def fgis_browse_source(
        source: str,
        parent: str | None = None,
        level: int | None = None,
        archive: bool = False,
        section: int | None = None,
        page: int = 1,
        limit: int = 20,
        offset: int = 0,
        source_params: dict[str, int | str | bool] | None = None,
    ) -> dict:
        """Navigate named FGIS source tree. Use returned GUID/id as parent and level for next branch.
        registry uses section + page; TER sections are 6 and 7. source_params supports
        region_id/zone_id/period_id/authority_id, stage, group_id, materials, year, fsnb_type.
        Current-price stages: authorities, services, worker_prices, group_indices, building_indices, direct_cost_indices.
        """
        return catalogs.browse(
            service.network, source, parent, level, archive, section, page, limit, offset, source_params
        )

    @server.tool(annotations=read)
    def fgis_diagnose() -> dict:
        """Check FGIS API JSON via configured route; explain proxy vs full-tunnel VPN limitations."""
        return service.network.diagnose()

    @server.tool(annotations=read)
    def fgis_catalog(kind: str = "regions", parent_id: int | None = None) -> dict:
        """List price regions; zones with region parent_id; periods with zone parent_id."""
        return service.catalog(kind, parent_id)

    @server.tool(annotations=read)
    def fgis_search_norms(
        query: str,
        limit: int = 20,
        offset: int = 0,
        family: str | None = None,
    ) -> dict:
        """Search public norm API by code/text. Returns cards for all returned source publications.
        Use this tool before claiming that a norm exists or before proposing a norm for a work item.
        Do not invent or infer norm codes absent from this result. Distinguishes exact, candidate, and not_found matches.
        Pass family (e.g. 'ГЭСН' or 'ГЭСНм') to filter results by norm collection family.
        """
        return service.online(query, limit, offset, family=family)

    @server.tool(annotations=read)
    def fgis_read_norm(
        code: str,
        limit: int = 20,
        offset: int = 0,
        family: str | None = None,
        document_guid: str | None = None,
    ) -> dict:
        """Read exact bare norm card online: code, name, unit, hierarchy (collection/dept/section/table),
        work steps, resources, mass, special indicators, separate editions and structured provenance.
        When the same numeric code exists in different collections/families (e.g. ГЭСН vs ГЭСНм),
        returns match_status='ambiguous' with options. Disambiguate by providing family (e.g. 'ГЭСН', 'ГЭСНм')
        and/or document_guid.
        Returns a compact self-contained card without duplicated data.
        Use this tool before claiming what a norm includes, its unit, work steps, resources or technical characteristics.
        Do NOT call fgis_read_document merely to inspect norm resources or work steps — use fgis_read_norm instead.
        """
        return service.read_norm(code, family=family, document_guid=document_guid)

    @server.tool(annotations=read)
    def fgis_batch_search_norms(
        items: list[dict],
    ) -> dict:
        """Search multiple queries for norm candidates in a single bounded batch (1..10 items).
        Each item must contain 'input_id' and 'query', and optionally 'family' (e.g. 'ГЭСН', 'ГЭСНм')
        and 'limit' (default 5, max 10).
        Always returns candidates only (match_status='candidate' or 'not_found'); never assigns 'exact'.
        Use this tool before proposing norms for a list of work items. Do not invent norm codes absent from results.
        Preserves input_id on every result. Errors in individual items are isolated.
        """
        return service.batch_search_norms(items)

    @server.tool(annotations=read)
    def fgis_batch_read_norms(
        items: list[dict],
        detail_level: str = "compact",
    ) -> dict:
        """Read and verify multiple exact bare norm cards online in a single bounded batch (1..10 items).
        Each item must contain 'input_id' and 'code', and optionally 'family' and 'document_guid'.
        Results are strictly independent: each item returns its own match_status
        ('exact', 'ambiguous', 'not_found', or 'error').
        When ambiguous, returns disambiguation options; when not_found, guards against hallucinated codes.
        detail_level can be 'compact' (default, lightweight card preserving work_steps and compact resources) or 'full'.
        Preserves input_id on every result. Errors in individual items are isolated.
        """
        return service.batch_read_norms(items, detail_level=detail_level)

    @server.tool(annotations=read)
    def fgis_read_document(
        document_guid: str | None = None,
        source: str = "normative",
        offset: int = 0,
        limit: int = 12000,
        expected_sha256: str | None = None,
        refresh: bool = False,
    ) -> dict:
        """Read surrounding normative document text online (technical part, introductory notes, application rules, annexes, table coefficients).
        Does NOT duplicate the norm card. Use fgis_read_norm to inspect norm resources/work steps.
        Use fgis_read_document when you need official document context: technical parts (техническая часть), general provisions (общие указания), application conditions, exceptions, notes, or coefficient justifications.
        Use document_refs from browse: source='normative' + GUID for FSNB/FER/methods/PIR;
        source='fssc'/'fsem' without GUID for those full documents. Offsets are text characters.
        Pass prior provenance.sha256 as expected_sha256 to guard against changed documents.
        A bounded 15-minute RAM cache avoids repeated full downloads. refresh forces a new request.
        Embedded images are not OCR-processed. Use table tool for cells and merged spans.
        """
        return service.documents.read(document_guid, source, offset, limit, expected_sha256, refresh)

    @server.tool(annotations=read)
    def fgis_search_document(
        query: str,
        document_guid: str | None = None,
        source: str = "normative",
        offset: int = 0,
        limit: int = 10,
        context: int = 200,
        expected_sha256: str | None = None,
    ) -> dict:
        """Find literal case-insensitive text within one public document, without a dataset.
        Use before making claims about methodology clauses, technical parts, application conditions, exceptions, notes, or normative justifications.
        Whitespace matches across paragraph/cell boundaries. Returns excerpts, character offsets and
        next_offset for more matches. Use read_document at a returned offset to read the surrounding text.
        No semantic search, coefficient selection or inference. Check text_status before interpreting no matches.
        """
        return service.documents.search(query, document_guid, source, offset, limit, context, expected_sha256)

    @server.tool(annotations=read)
    def fgis_document_outline(
        document_guid: str | None = None,
        source: str = "normative",
        offset: int = 0,
        limit: int = 30,
        expected_sha256: str | None = None,
    ) -> dict:
        """List document paragraphs, explicit HTML headings and tables with text offsets and previews.
        offset/next_block_offset paginate blocks, not text characters. This is source structure,
        not an inferred official table of contents. Table indices feed fgis_read_document_table and fgis_extract_coefficients.
        """
        return service.documents.outline(document_guid, source, offset, limit, expected_sha256)

    @server.tool(annotations=read)
    def fgis_read_document_table(
        table_index: int,
        document_guid: str | None = None,
        source: str = "normative",
        row_offset: int = 0,
        limit: int = 20,
        expected_sha256: str | None = None,
        cell_offset: int = 0,
        cell_limit: int = 20,
    ) -> dict:
        """Read source table rows/cells online, preserving header flags, rowspan and colspan.
        Get table_index from outline. Each row paginates physical source cells with cell_offset/cell_limit;
        merged cells are not expanded. Long cell previews are explicitly truncated: read their text offsets
        with read_document. Includes surrounding text; no inferred price or coefficient applicability.
        """
        return service.documents.table(
            table_index, document_guid, source, row_offset, limit, expected_sha256, cell_offset, cell_limit
        )

    @server.tool(annotations=write)
    def fgis_start_download(
        queries: list[str] | None = None,
        collections: list[int] | None = None,
        price_books: list[dict[str, int]] | None = None,
        sources: list[str] | None = None,
        include_archive: bool = False,
        all_periods: bool = False,
        opendata_version: str | None = None,
        max_tasks: int = 25000,
    ) -> dict:
        """Start durable background download into a local dataset.
        Explicit queries, collection prefixes (each scans 01..99),
        and price_books list of zone/period pairs.
        IMPORTANT: there are NO 'price_zone_ids' or 'period_ids' arguments!
        Zone and period pairs must be passed strictly via 'price_books', for example:
            fgis_start_download(
                price_books=[
                    {"zone_id": 206, "period_id": 426},
                    {"zone_id": 206, "period_id": 427}
                ]
            )
        sources from fgis_sources traverses actual catalogues and downloads documents with technical parts.
        ['all_public'] selects all adapters; include_archive adds archived legal trees; all_periods includes
        historical split forms (large); opendata_version downloads only the specified FSNB snapshot/GUID/filename.
        max_tasks bounds traversal; bounded jobs retain pending tasks.
        TER registry is not TER table content; bulk archive files require interactive portal CAPTCHA.
        Search enumeration cannot establish exhaustive FSNB coverage. Returns job/dataset ID.
        """
        return jobs.start(
            config,
            queries,
            collections,
            price_books,
            sources=sources,
            include_archive=include_archive,
            all_periods=all_periods,
            opendata_version=opendata_version,
            max_tasks=max_tasks,
        )

    @server.tool(annotations=local)
    def fgis_job_status(job_id: str) -> dict:
        """Read progress, failed tasks and interruption state of a background download."""
        return jobs.status(config, job_id)

    @server.tool(annotations=write)
    def fgis_cancel_job(job_id: str) -> dict:
        """Request cooperative cancellation after the current request/import; preserve downloaded data."""
        return jobs.cancel(config, job_id)

    @server.tool(annotations=write)
    def fgis_resume_job(job_id: str, max_tasks: int | None = None) -> dict:
        """Resume a stopped/partial job, verify saved source hashes, retry uncommitted tasks."""
        return jobs.launch(config, job_id, max_tasks)

    @server.tool(annotations=local)
    def fgis_read_dataset_document(
        dataset_id: str, document_id: str, offset: int = 0, limit: int = 12000
    ) -> dict:
        """Read saved complete source JSON/technical parts in bounded character pages; no network."""
        return Dataset(config.root, dataset_id).read_document(document_id, offset, limit)

    @server.tool(annotations=local)
    def fgis_list_datasets(limit: int = 20, offset: int = 0) -> dict:
        """List locally stored datasets without contacting FGIS."""
        return service.datasets(limit, offset)

    @server.tool(annotations=local)
    def fgis_dataset_info(dataset_id: str) -> dict:
        """Read dataset counts, coverage, manifest and local artifact paths."""
        return service.dataset_info(dataset_id)

    @server.tool(annotations=local)
    def fgis_query_dataset(
        dataset_id: str,
        kind: str = "norms",
        query: str = "",
        code: str = "",
        limit: int = 20,
        offset: int = 0,
        zone_id: int | None = None,
        period_id: int | None = None,
        family: str | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Offline norms/prices/documents/fsbc search in a local dataset.
        kind can be 'norms', 'prices', 'documents', or 'fsbc'.
        kind='fsbc' searches local FSBC resource cards by code or text.
        For kind='norms', family filters by collection family (e.g. 'ГЭСН', 'ГЭСНм').
        For tracking the history of a resource card across snapshot editions, prefer fgis_price_history,
        which aggregates base_records across all imported snapshots.
        Documents return summaries; read full content with document tool.
        """
        return Dataset(config.root, dataset_id).query(
            kind,
            query,
            code,
            limit,
            offset,
            zone_id,
            period_id,
            family=family,
            include_incomplete=include_incomplete,
        )

    @server.tool(annotations=write)
    def fgis_export_dataset(
        dataset_id: str,
        formats: list[str] | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Export stopped dataset to jsonl and/or parquet. SQLite always exists; return files and hashes."""
        return service.export(
            dataset_id,
            formats if formats is not None else ["jsonl", "parquet"],
            include_incomplete=include_incomplete,
        )

    @server.tool(annotations=read)
    def fgis_compare_norms(
        code: str,
        edition_a: str | None = None,
        edition_b: str | None = None,
        dataset_id: str | None = None,
        family: str | None = None,
        document_guid: str | None = None,
    ) -> dict:
        """Compare two editions or publications of a norm code: differences in work steps, resources, units.
        Use to verify additions, removals, changes, or invariances between norm versions.
        Specify family (e.g. 'ГЭСН' or 'ГЭСНм') or document_guid when the code exists in multiple families.
        """
        return service.compare_norms(
            code,
            edition_a,
            edition_b,
            dataset_id,
            family=family,
            document_guid=document_guid,
        )

    @server.tool(annotations=read)
    def fgis_extract_coefficients(
        document_guid: str | None = None,
        source: str = "normative",
        table_index: int | None = None,
    ) -> dict:
        """Extract structured coefficient evidence and conditions from document technical parts.
        Use this tool after identifying a real document/table via fgis_browse_source, fgis_search_document or fgis_document_outline.
        Requires document_guid and/or table_index; does NOT accept a search query argument.
        Preserves condition text, note text, multipliers. If structure is ambiguous, returns status='unresolved'.
        """
        return service.extract_coefficients(document_guid, source, table_index)

    @server.tool(annotations=read)
    def fgis_price_history(
        code: str,
        dataset_id: str | None = None,
        zone_id: int | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Query resource price timeline and FSBC base card evolution across editions and periods in a dataset.
        Returns two distinct sets of records that must NOT be mixed:
        1. base_records: history of the base FSBC resource card across imported editions/snapshots.
           Shows official resource name, unit of measurement, base prices (price_base, price_release),
           resource_type, snapshot_id/snapshot_uid, and provenance. Use base_records to track resource
           name and unit changes between FSNB/FSBC editions.
        2. quarterly_records: quarterly estimated and current prices from regional split forms for zone_id
           and period_id.
        available_periods lists all quarterly periods present in the dataset.
        Absence of quarterly price records in the local dataset does NOT prove absence in FGIS CS;
        unimported periods/zones can be downloaded via fgis_start_download.
        """
        return service.price_history(code, dataset_id, zone_id, include_incomplete=include_incomplete)

    @server.tool(annotations=local)
    def fgis_verify_dataset(dataset_id: str) -> dict:
        """Strictly audit dataset completeness: verify totalCount proofs, task integrity, coverage matrix."""
        return service.verify_dataset(dataset_id)

    @server.tool(annotations=write)
    def fgis_import_manual_file(
        dataset_id: str,
        file_path: str,
        source: str = "ter",
        edition: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Import manually downloaded official TER or archive file into a dataset with SHA-256 provenance."""
        return service.import_manual_file(dataset_id, file_path, source, edition, note)

    @server.tool(annotations=read)
    def fgis_norm_history(
        code: str,
        dataset_id: str | None = None,
        family: str | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Retrieve complete historical editions of a norm across all imported snapshots with transition diffs.
        Use this tool before making claims about changes between FSNB editions.
        """
        return service.norm_history(code, dataset_id, family=family, include_incomplete=include_incomplete)

    @server.tool(annotations=read)
    def fgis_compare_snapshots(
        snapshot_a: str,
        snapshot_b: str,
        dataset_id: str | None = None,
        family: str | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Compare two entire FSNB editions in a dataset: counts of added, removed, modified, and identical norms."""
        return service.compare_snapshots(
            snapshot_a, snapshot_b, dataset_id, family, include_incomplete=include_incomplete
        )

    @server.tool(annotations=write)
    def fgis_import_opendata(
        archive_path: str,
        dataset_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict:
        """Import an official OpenData FSNB/FSBC ZIP distribution archive with streaming XML parsing."""
        return service.import_opendata_archive(archive_path, dataset_id, snapshot_id)

    @server.tool(annotations=read)
    def fgis_opendata_list() -> dict:
        """List official OpenData datasets and passports (FSNB-2022, FSNB-2020 / FER)."""
        return service.opendata_list()

    @server.tool(annotations=read)
    def fgis_opendata_get(dataset_number: str) -> dict:
        """Fetch official OpenData passport metadata, versions, and file distributions."""
        return service.opendata_get(dataset_number)

    @server.resource("fgis://help")
    def help_resource() -> str:
        return dump(
            {
                "schema": "fgis.dataset.v1",
                "principles": [
                    "No evidence from MCP -> no fact in answer (UNSUPPORTED_BY_FGIS_MCP)",
                    "Search first: do not invent or guess norm codes",
                    "Read specific norm before describing work steps or resources",
                    "Read official documents before citing clauses or application conditions",
                    "Distinguish candidates from exact matches",
                    "Distinguish local dataset incompleteness from absence in FGIS CS",
                ],
                "network": config.network,
                "online_workflow": [
                    "fgis_browse_source",
                    "fgis_read_document",
                    "fgis_search_document",
                    "fgis_document_outline",
                    "fgis_read_document_table",
                    "fgis_extract_coefficients",
                ],
                "workflow": [
                    "fgis_diagnose",
                    "fgis_catalog",
                    "fgis_start_download",
                    "fgis_job_status",
                    "fgis_query_dataset",
                    "fgis_export_dataset",
                ],
                "coverage": "Full FSNB coverage is never inferred from search success",
            }
        )

    return server
