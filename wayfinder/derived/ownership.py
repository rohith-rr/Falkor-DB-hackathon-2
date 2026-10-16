from ..models.common import now_iso


def covering_directory_ids(db, directory_id, as_of=None):
    """Walk ancestors, stopping after a directory with no_parent_owners."""
    at = as_of or now_iso()
    result, seen = [], set()
    current = directory_id
    while current and current not in seen:
        seen.add(current)
        result.append(current)
        blocked = db.query('MATCH (o:OwnersFile)-[r:COVERS]->(d:Directory {id: $id}) '
                           'WHERE o.no_parent_owners = true AND r.valid_from <= $at '
                           'AND (r.valid_to IS NULL OR r.valid_to > $at) RETURN o.id', {'id': current, 'at': at})
        if blocked.result_set:
            break
        parent = db.query('MATCH (d:Directory {id: $id})-[r:CHILD_OF]->(p:Directory) '
                          'WHERE r.valid_from <= $at AND (r.valid_to IS NULL OR r.valid_to > $at) RETURN p.id',
                          {'id': current, 'at': at})
        current = parent.result_set[0][0] if parent.result_set else None
    return result
