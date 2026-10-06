local roads = osm2pgsql.define_way_table('roads', {
    { column = 'name', type = 'text' },
    { column = 'highway', type = 'text' },
    { column = 'ref', type = 'text' },
    { column = 'maxspeed', type = 'text' },
    { column = 'toll', type = 'text' },
    { column = 'oneway', type = 'text' },
    { column = 'surface', type = 'text' },
    { column = 'geom', type = 'linestring' },
})

function osm2pgsql.process_way(object)

    local highway = object.tags.highway

    if not highway then
        return
    end

    local allowed = {
        motorway = true,
        motorway_link = true,
        trunk = true,
        trunk_link = true,
        primary = true,
        primary_link = true,
        secondary = true,
        secondary_link = true,
        tertiary = true,
        tertiary_link = true,
        residential = true,
        living_street = true,
        service = true,
        unclassified = true,
    }

    if not allowed[highway] then
        return
    end

    roads:insert({
        name = object.tags.name,
        highway = highway,
        ref = object.tags.ref,
        maxspeed = object.tags.maxspeed,
        toll = object.tags.toll,
        oneway = object.tags.oneway,
        surface = object.tags.surface,
        geom = object:as_linestring(),
    })
end
