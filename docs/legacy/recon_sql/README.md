# Legacy Recon SQL - Reference Only

These files were imported from Recon V1 for historical comparison. They are
not package resources, are not read by the root migration runner, and must not
be applied automatically.

Some scripts contain destructive cleanup (`DELETE`, `DROP`, data rewrites) or
belong to the deferred GSC/GA4 subsystem. Supported schema changes must be
implemented as new checksummed, append-only files under the root `migrations/`
directory after a live read-only schema audit.
