"""
Database Context Service

Provides safe, function-based database access for project databases.

Security principles:
- NO arbitrary SQL execution (prevents PII leaks)
- Function-based API only (get_schema_metadata, get_table_row_counts, etc.)
- Respects db_context_mode (none, metadata_only, readonly)
- PII field redaction in sample data
- Audit logging for all access
- Read-only database user (ralph_ro)

Used by MCP tools to provide Claude with project database context.
"""

import asyncpg
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.services.secret_resolver import get_resolver, SecretsProvider
from app.models.project import ProjectRegistry, DBContextMode

logger = logging.getLogger(__name__)


class DBContextError(Exception):
    """Raised when database context operation fails."""
    pass


class DBContextService:
    """
    Safe database context service.

    Provides metadata and limited data access to project databases.
    All access is audited and respects configured security boundaries.
    """

    def __init__(self, db):
        """
        Initialize DB context service.

        Args:
            db: Ralph's own database session (for audit logging)
        """
        self.db = db
        self.resolver = get_resolver()

    async def _get_project_connection(self, project: ProjectRegistry) -> asyncpg.Connection:
        """
        Get connection to project database.

        Args:
            project: Project registry entry

        Returns:
            asyncpg connection to project database

        Raises:
            DBContextError: If connection fails or db_context_mode is 'none'
        """
        if project.db_context_mode == DBContextMode.NONE:
            raise DBContextError(
                f"Project {project.project_id} has db_context_mode=none. "
                f"Database access is disabled."
            )

        if not project.db_connection_ref:
            raise DBContextError(
                f"Project {project.project_id} has no db_connection_ref configured"
            )

        try:
            db_url = self.resolver.resolve_db_url(
                project.secrets_provider,
                project.db_connection_ref
            )

            if not db_url:
                raise DBContextError(f"Failed to resolve database URL for project {project.project_id}")

            # Connect using read-only credentials
            # Format: postgresql://ralph_ro:password@host/db
            # Assumes ralph_ro user is created with SELECT-only grants
            conn = await asyncpg.connect(db_url)
            return conn

        except Exception as e:
            logger.error(f"Failed to connect to project database {project.project_id}: {e}")
            raise DBContextError(f"Database connection failed: {str(e)}")

    async def _log_access(
        self,
        project_id: str,
        build_id: Optional[str],
        access_mode: str,
        query_count: int,
        row_count: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None
    ):
        """
        Log database access to audit trail.

        Args:
            project_id: Project identifier
            build_id: Build identifier (if applicable)
            access_mode: 'metadata' or 'readonly'
            query_count: Number of queries executed
            row_count: Total rows returned (if applicable)
            duration_ms: Operation duration in milliseconds
            error_message: Error message if failed
        """
        try:
            await self.db.execute("""
                INSERT INTO ralph_db_access_log (
                    project_id, build_id, access_mode, query_count,
                    row_count, duration_ms, error_message
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, project_id, build_id, access_mode, query_count, row_count, duration_ms, error_message)
            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log DB access: {e}")

    def _redact_pii(self, row: Dict[str, Any], pii_fields: Optional[List[str]]) -> Dict[str, Any]:
        """
        Redact PII fields from row data.

        Args:
            row: Row data as dict
            pii_fields: List of field names to redact

        Returns:
            Row with PII fields replaced with '[REDACTED]'
        """
        if not pii_fields:
            return row

        redacted_row = dict(row)
        for field in pii_fields:
            if field in redacted_row:
                redacted_row[field] = "[REDACTED]"

        return redacted_row

    async def get_schema_metadata(
        self,
        project: ProjectRegistry,
        build_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get database schema metadata (schemas, tables, columns).

        Requires: db_context_mode >= metadata_only

        Args:
            project: Project registry entry
            build_id: Optional build identifier for audit logging

        Returns:
            Schema metadata dict with format:
            {
                "schemas": [
                    {
                        "name": "public",
                        "tables": [
                            {
                                "name": "users",
                                "columns": [
                                    {"name": "id", "type": "integer"},
                                    {"name": "email", "type": "varchar"}
                                ]
                            }
                        ]
                    }
                ]
            }

        Raises:
            DBContextError: If access denied or operation fails
        """
        if project.db_context_mode == DBContextMode.NONE:
            raise DBContextError("Database access disabled for this project")

        start_time = datetime.now()
        conn = None

        try:
            conn = await self._get_project_connection(project)

            # Get schemas (filter by allowed_schemas if configured)
            if project.allowed_schemas:
                schema_list = project.allowed_schemas
            else:
                # Default: exclude system schemas
                schemas = await conn.fetch("""
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                    ORDER BY schema_name
                """)
                schema_list = [s['schema_name'] for s in schemas]

            result_schemas = []

            for schema_name in schema_list:
                # Get tables in schema (filter by allowed_tables if configured)
                if project.allowed_tables:
                    table_list = [t for t in project.allowed_tables if '.' not in t or t.startswith(f"{schema_name}.")]
                else:
                    tables = await conn.fetch("""
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = $1 AND table_type = 'BASE TABLE'
                        ORDER BY table_name
                    """, schema_name)
                    table_list = [t['table_name'] for t in tables]

                schema_tables = []

                for table_name in table_list:
                    # Strip schema prefix if present
                    if '.' in table_name:
                        table_name = table_name.split('.', 1)[1]

                    # Get columns
                    columns = await conn.fetch("""
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = $1 AND table_name = $2
                        ORDER BY ordinal_position
                    """, schema_name, table_name)

                    schema_tables.append({
                        "name": table_name,
                        "columns": [
                            {
                                "name": col['column_name'],
                                "type": col['data_type'],
                                "nullable": col['is_nullable'] == 'YES'
                            }
                            for col in columns
                        ]
                    })

                result_schemas.append({
                    "name": schema_name,
                    "tables": schema_tables
                })

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Audit log
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=len(schema_list) * 2,  # Approximate
                duration_ms=duration_ms
            )

            return {"schemas": result_schemas}

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=0,
                duration_ms=duration_ms,
                error_message=str(e)
            )
            raise DBContextError(f"Failed to get schema metadata: {str(e)}")

        finally:
            if conn:
                await conn.close()

    async def get_table_row_counts(
        self,
        project: ProjectRegistry,
        build_id: Optional[str] = None
    ) -> Dict[str, int]:
        """
        Get row counts for all tables.

        Requires: db_context_mode >= metadata_only

        Args:
            project: Project registry entry
            build_id: Optional build identifier for audit logging

        Returns:
            Dict mapping table names to row counts:
            {
                "public.users": 1234,
                "public.products": 5678
            }

        Raises:
            DBContextError: If access denied or operation fails
        """
        if project.db_context_mode == DBContextMode.NONE:
            raise DBContextError("Database access disabled for this project")

        start_time = datetime.now()
        conn = None

        try:
            conn = await self._get_project_connection(project)

            # Get table list from metadata
            metadata = await self.get_schema_metadata(project, build_id)

            counts = {}
            query_count = 0

            for schema in metadata['schemas']:
                for table in schema['tables']:
                    schema_name = schema['name']
                    table_name = table['name']
                    full_name = f"{schema_name}.{table_name}"

                    # COUNT(*) query
                    result = await conn.fetchrow(f'SELECT COUNT(*) as count FROM "{schema_name}"."{table_name}"')
                    counts[full_name] = result['count']
                    query_count += 1

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Audit log
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=query_count,
                duration_ms=duration_ms
            )

            return counts

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=0,
                duration_ms=duration_ms,
                error_message=str(e)
            )
            raise DBContextError(f"Failed to get row counts: {str(e)}")

        finally:
            if conn:
                await conn.close()

    async def get_migration_version(
        self,
        project: ProjectRegistry,
        build_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get Alembic migration version (if Alembic is used).

        Requires: db_context_mode >= metadata_only

        Args:
            project: Project registry entry
            build_id: Optional build identifier for audit logging

        Returns:
            Current migration version or None if no alembic_version table

        Raises:
            DBContextError: If access denied or operation fails
        """
        if project.db_context_mode == DBContextMode.NONE:
            raise DBContextError("Database access disabled for this project")

        start_time = datetime.now()
        conn = None

        try:
            conn = await self._get_project_connection(project)

            # Check if alembic_version table exists
            table_exists = await conn.fetchrow("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'alembic_version'
                )
            """)

            if not table_exists['exists']:
                return None

            # Get current version
            version = await conn.fetchrow("SELECT version_num FROM alembic_version")

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Audit log
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=2,
                duration_ms=duration_ms
            )

            return version['version_num'] if version else None

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='metadata',
                query_count=0,
                duration_ms=duration_ms,
                error_message=str(e)
            )
            raise DBContextError(f"Failed to get migration version: {str(e)}")

        finally:
            if conn:
                await conn.close()

    async def get_sample_data(
        self,
        project: ProjectRegistry,
        table_name: str,
        limit: int = 10,
        build_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get sample rows from a table (with PII redaction).

        Requires: db_context_mode = readonly

        Args:
            project: Project registry entry
            table_name: Table name (format: "schema.table" or "table")
            limit: Maximum rows to return (capped at 100)
            build_id: Optional build identifier for audit logging

        Returns:
            List of rows as dicts (PII fields redacted)

        Raises:
            DBContextError: If access denied or operation fails
        """
        if project.db_context_mode != DBContextMode.READONLY:
            raise DBContextError(
                "Sample data access requires db_context_mode=readonly. "
                f"Current mode: {project.db_context_mode.value}"
            )

        # Cap limit at 100
        limit = min(limit, 100)

        start_time = datetime.now()
        conn = None

        try:
            conn = await self._get_project_connection(project)

            # Parse table name
            if '.' in table_name:
                schema_name, table_only = table_name.split('.', 1)
            else:
                schema_name = 'public'
                table_only = table_name

            # Verify table is allowed
            if project.allowed_tables:
                full_name = f"{schema_name}.{table_only}"
                if full_name not in project.allowed_tables and table_only not in project.allowed_tables:
                    raise DBContextError(f"Table {full_name} is not in allowed_tables list")

            # Query sample data
            rows = await conn.fetch(f'SELECT * FROM "{schema_name}"."{table_only}" LIMIT $1', limit)

            # Convert to dicts and redact PII
            result = []
            for row in rows:
                row_dict = dict(row)
                redacted = self._redact_pii(row_dict, project.pii_fields)
                result.append(redacted)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Audit log
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='readonly',
                query_count=1,
                row_count=len(result),
                duration_ms=duration_ms
            )

            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            await self._log_access(
                project_id=project.project_id,
                build_id=build_id,
                access_mode='readonly',
                query_count=0,
                duration_ms=duration_ms,
                error_message=str(e)
            )
            raise DBContextError(f"Failed to get sample data: {str(e)}")

        finally:
            if conn:
                await conn.close()
