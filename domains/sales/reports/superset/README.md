# Sales Superset Reports

This directory is the source-controlled Superset report bundle for the Sales
domain.

`make superset-reports-deploy` zips these YAML files, copies the bundle into the
Superset reports volume, and imports the assets into Superset. UI edits persist
in Superset metadata, but durable changes should be exported back here with
`make superset-reports-export`.
