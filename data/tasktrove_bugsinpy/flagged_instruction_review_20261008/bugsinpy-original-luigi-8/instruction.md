In the Luigi repo at `/app`, `S3CopyToTable.does_table_exist` in the Redshift contrib module does not handle mixed-case table names. Redshift stores unquoted identifiers in lowercase, so if a task's `table` is something like `MySchema.MyTable` or `MyTable`, the existence check finds nothing. The task then keeps trying to create a table that already exists.

Change the existence check so the lookup ignores case. Do the lowercasing in SQL, not in Python: pass the schema and table names to `cursor.execute` unchanged, as `tuple(self.table.split('.'))`, and apply `lower()` to each placeholder in the query. The query text must match these exactly:

- Schema-qualified table (`table` contains a `.`):
  `"select 1 as table_exists from information_schema.tables where table_schema = lower(%s) and table_name = lower(%s) limit 1"`
- Unqualified table:
  `"select 1 as table_exists from pg_table_def where tablename = lower(%s) limit 1"`

Leave everything else about the method as it is.
