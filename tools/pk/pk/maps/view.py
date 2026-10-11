from ..core.out import Result, page


def listing(repo, query='', offset=0, limit=20):
    layouts = repo.layouts()
    rows = []
    for name, data in repo.maps().items():
        if query.casefold() not in (name+' '+data['id']+' '+data['layout']).casefold():
            continue
        layout = layouts[data['layout']]
        rows.append(f"{name} {layout['width']}x{layout['height']} {layout['primary_tileset']}/{layout['secondary_tileset']}")
    return page(f'maps {len(rows)}', rows, offset, limit)


def info(repo, query, offset=0, limit=6):
    name, data, layout = repo.map(query)
    counts = ' '.join(f"{short}:{len(data.get(key, []))}" for short, key in
                      [('obj', 'object_events'), ('warp', 'warp_events'), ('coord', 'coord_events'), ('bg', 'bg_events')])
    rows = [f"conn {c['direction']} {c['map']} offset={c['offset']}" for c in data.get('connections') or []]
    rows += [f"warp#{n} @{w['x']},{w['y']} -> {w['dest_map']}#{w['dest_warp_id']}" for n, w in enumerate(data.get('warp_events', []))]
    result = page(name, rows, offset, limit, maximum=6)
    result.data = [f"id {data['id']}", f"layout {data['layout']} {repo.version(layout)}",
                   f"size {layout['width']}x{layout['height']}",
                   f"tilesets {layout['primary_tileset']} {layout['secondary_tileset']}",
                   f"music {data['music']} weather {data['weather']}", f"type {data['map_type']}",
                   f'events {counts}'] + result.data
    return result
