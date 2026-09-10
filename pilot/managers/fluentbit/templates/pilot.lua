-- Pilot -> Datum normalizer

local function json_escape(value)
    if value == nil then
        return ""
    end

    value = tostring(value)

    value = string.gsub(value, "\\", "\\\\")
    value = string.gsub(value, "\"", "\\\"")
    value = string.gsub(value, "\b", "\\b")
    value = string.gsub(value, "\f", "\\f")
    value = string.gsub(value, "\n", "\\n")
    value = string.gsub(value, "\r", "\\r")
    value = string.gsub(value, "\t", "\\t")

    return value
end


local function json_string(value)
    return "\"" .. json_escape(value) .. "\""
end


local function serialize_attributes(attributes)
    local parts = {}

    for key, value in pairs(attributes) do
        table.insert(
            parts,
            json_string(key) .. ":" .. json_string(value)
        )
    end

    return "{" .. table.concat(parts, ",") .. "}"
end


-- Extract filename from Path_Key

local function get_source(filename)
    if filename == nil then
        return "unknown"
    end

    local source = string.match(
        filename,
        "/([^/]+)$"
    )

    return source or "unknown"
end


-- Filename -> service

local function get_service(source)

    if source == "nginx-access.log"
        or source == "nginx-error.log" then

        return "nginx"
    end

    local service = string.match(
        source,
        "^(.*)%.json%.log$"
    )

    if service ~= nil then
        return service
    end

    service = string.match(
        source,
        "^(.*)%.log$"
    )

    if service ~= nil then
        return service
    end

    return source
end


-- Python timestamp
--
-- 2026-07-29 07:14:51,626
-- ->
-- 2026-07-29T07:14:51.626Z

local function python_timestamp(value)

    if value == nil then
        return nil
    end

    local year, month, day,
          hour, minute, second, millis =
        string.match(
            value,
            "^(%d%d%d%d)-(%d%d)-(%d%d) (%d%d):(%d%d):(%d%d),(%d%d%d)$"
        )

    if year == nil then
        return value
    end

    return string.format(
        "%s-%s-%sT%s:%s:%s.%sZ",
        year,
        month,
        day,
        hour,
        minute,
        second,
        millis
    )
end


-- Nginx error timestamp

local function nginx_error_timestamp(value)

    if value == nil then
        return nil
    end

    local year, month, day,
          hour, minute, second =
        string.match(
            value,
            "^(%d%d%d%d)/(%d%d)/(%d%d) (%d%d):(%d%d):(%d%d)$"
        )

    if year == nil then
        return value
    end

    return string.format(
        "%s-%s-%sT%s:%s:%s.000Z",
        year,
        month,
        day,
        hour,
        minute,
        second
    )
end


-- Nginx access timestamp
--
-- 21/Jul/2026:07:25:38 +0000
-- ->
-- 2026-07-21T07:25:38.000+00:00

local months = {
    Jan = "01",
    Feb = "02",
    Mar = "03",
    Apr = "04",
    May = "05",
    Jun = "06",
    Jul = "07",
    Aug = "08",
    Sep = "09",
    Oct = "10",
    Nov = "11",
    Dec = "12"
}


local function nginx_access_timestamp(value)

    if value == nil then
        return nil
    end

    local day, month_name, year,
          hour, minute, second, offset =
        string.match(
            value,
            "^(%d%d)/(%a%a%a)/(%d%d%d%d):(%d%d):(%d%d):(%d%d) ([+-]%d%d%d%d)$"
        )

    if day == nil then
        return value
    end

    local month = months[month_name]

    if month == nil then
        return value
    end

    return string.format(
        "%s-%s-%sT%s:%s:%s.000%s:%s",
        year,
        month,
        day,
        hour,
        minute,
        second,
        string.sub(offset, 1, 3),
        string.sub(offset, 4, 5)
    )
end


-- Redis timestamp
--
-- Redis parser:
--   date = "21 Jul 2026"
--   time = "07:20:39.665"

local redis_months = {
    Jan = "01",
    Feb = "02",
    Mar = "03",
    Apr = "04",
    May = "05",
    Jun = "06",
    Jul = "07",
    Aug = "08",
    Sep = "09",
    Oct = "10",
    Nov = "11",
    Dec = "12"
}


local function redis_timestamp(record)

    local date = record["date"]
    local time = record["time"]

    if date == nil or time == nil then
        return nil
    end

    local day, month_name, year =
        string.match(
            date,
            "^(%d%d) (%a%a%a) (%d%d%d%d)$"
        )

    if day == nil then
        return nil
    end

    local month = redis_months[month_name]

    if month == nil then
        return nil
    end

    return string.format(
        "%s-%s-%sT%sZ",
        year,
        month,
        day,
        time
    )
end


-- Worker pool timestamp
--
-- Worker pool only gives HH:MM:SS.
-- Use Fluent Bit's record timestamp for the date.

local function worker_pool_timestamp(timestamp, value)

    if value == nil then
        return nil
    end

    local hour, minute, second =
        string.match(
            value,
            "^(%d%d):(%d%d):(%d%d)$"
        )

    if hour == nil then
        return nil
    end

    return os.date(
        "!%Y-%m-%dT",
        math.floor(timestamp)
    )
    .. string.format(
        "%s:%s:%s.000Z",
        hour,
        minute,
        second
    )
end


-- Level normalization
--
-- We NEVER infer severity from message text.
-- If the source does not provide a level, use info.

local function normalize_level(level)

    if level == nil or level == "" then
        return "info"
    end

    return string.lower(
        tostring(level)
    )
end


-- Add a field to attributes if present

local function add_attribute(attributes, key, value)

    if value ~= nil then
        attributes[key] = tostring(value)
    end
end


-- Datum request body

local function build_body(
    ts,
    level,
    message,
    service,
    source,
    attributes
)

    local line =
        "{"
        .. "\"ts\":" .. json_string(ts)
        .. ",\"level\":" .. json_string(level)
        .. ",\"message\":" .. json_string(message)
        .. ",\"service\":" .. json_string(service)
        .. ",\"source\":" .. json_string(source)
        .. ",\"product\":\"pilot\""
        .. ",\"attributes\":" .. serialize_attributes(attributes)
        .. "}"

    return "{\"lines\":[" .. line .. "]}"
end


-- Main

function normalize(tag, timestamp, record)

    local filename = record["filename"]

    local source = get_source(filename)
    local service = get_service(source)

    local ts


    -- Timestamp

    if tag == "pilot.python" then

        ts = python_timestamp(
            record["time"]
        )

    elseif tag == "pilot.nginx.access" then

        ts = nginx_access_timestamp(
            record["time"]
        )

    elseif tag == "pilot.nginx.error" then

        ts = nginx_error_timestamp(
            record["time"]
        )

    elseif tag == "pilot.redis" then

        ts = redis_timestamp(
            record
        )

    elseif tag == "pilot.worker_pool" then

        ts = worker_pool_timestamp(
            timestamp,
            record["time"]
        )

    elseif tag == "pilot.json" then

        ts = record["time"]
    end


    if ts == nil then
        return -1, timestamp, record
    end


    -- Fixed Datum fields

    local level = normalize_level(
        record["level"]
    )

    local message = record["message"] or ""

    local attributes = {}


    -- Python / Frappe
    --
    -- Fixed:
    --   ts
    --   level
    --   message
    --   service
    --   source
    --   product
    --
    -- Parsed:
    --   logger -> attributes

    if tag == "pilot.python" then

        add_attribute(
            attributes,
            "logger",
            record["logger"]
        )


    -- JSONL
    --
    -- Everything except Datum's fixed fields becomes an
    -- attribute.

    elseif tag == "pilot.json" then

        if record["level"] == nil then
            level = "info"
        end

        if record["message"] == nil then
            message = source
        end

        for key, value in pairs(record) do

            if key ~= "filename"
                and key ~= "time"
                and key ~= "level"
                and key ~= "message"
                and key ~= "ts"
                and key ~= "product"
                and key ~= "service"
                and key ~= "source"
                and key ~= "attributes"
                and key ~= "body"
                and key ~= "headers" then

                add_attribute(
                    attributes,
                    key,
                    value
                )
            end
        end


    -- Nginx access

    elseif tag == "pilot.nginx.access" then

        level = "info"

        message =
            (record["method"] or "")
            .. " "
            .. (record["path"] or "")

        add_attribute(
            attributes,
            "client_ip",
            record["client_ip"]
        )

        add_attribute(
            attributes,
            "method",
            record["method"]
        )

        add_attribute(
            attributes,
            "path",
            record["path"]
        )

        add_attribute(
            attributes,
            "status",
            record["status"]
        )

        add_attribute(
            attributes,
            "upstream_ip",
            record["upstream_ip"]
        )

        add_attribute(
            attributes,
            "response_time",
            record["response_time"]
        )


    -- Nginx error

    elseif tag == "pilot.nginx.error" then

        level = normalize_level(
            record["level"]
        )

        message = record["message"] or ""

        local fields = {
            "pid",
            "connection",
            "client_ip",
            "server",
            "method",
            "request",
            "http_version",
            "upstream",
            "host",
            "referrer"
        }

        for _, key in ipairs(fields) do

            add_attribute(
                attributes,
                key,
                record[key]
            )
        end


    -- Redis
    --
    -- Redis severity markers:
    --
    --   # -> warn
    --   * -> info
    --   - -> info
    --   . -> debug
    --
    -- If an unknown marker appears, use info.
    -- We do NOT infer severity from the message.

    elseif tag == "pilot.redis" then

        local redis_levels = {
            ["#"] = "warn",
            ["*"] = "info",
            ["-"] = "info",
            ["."] = "debug"
        }

        level =
            redis_levels[
                tostring(record["level"])
            ]
            or "info"

        message =
            record["message"]
            or ""

        add_attribute(
            attributes,
            "pid",
            record["pid"]
        )

        add_attribute(
            attributes,
            "role",
            record["role"]
        )

        add_attribute(
            attributes,
            "date",
            record["date"]
        )

        add_attribute(
            attributes,
            "time",
            record["time"]
        )


    -- Worker pool
    --
    -- Examples:
    --
    -- Starting worker pool abc with pid 15463...
    --
    -- Worker abc: started with PID 15491, version 2.6.1
    --
    -- Worker abc: subscribing to channel rq:pubsub:abc
    --
    -- *** Listening on queue1, queue2...
    --
    -- Worker abc: cleaning registries for queue: queue1
    --
    -- Received SIGINT/SIGTERM, shutting down...
    --
    -- Sent shutdown command to worker with 15491
    --
    -- Worker pool has no severity field.
    -- Therefore EVERYTHING is info.

    elseif tag == "pilot.worker_pool" then

        level = "info"

        message =
            record["message"]
            or ""


        -- Starting worker pool

        local pool_id, pid =
            string.match(
                message,
                "^Starting worker pool (%S+) with pid (%d+)"
            )

        if pool_id ~= nil then

            add_attribute(
                attributes,
                "pool_id",
                pool_id
            )

            add_attribute(
                attributes,
                "pid",
                pid
            )
        end


        -- Worker started

        local worker_id, worker_pid, version =
            string.match(
                message,
                "^Worker (%S+): started with PID (%d+), version (%S+)"
            )

        if worker_id ~= nil then

            add_attribute(
                attributes,
                "worker_id",
                worker_id
            )

            add_attribute(
                attributes,
                "pid",
                worker_pid
            )

            add_attribute(
                attributes,
                "version",
                version
            )
        end


        -- Worker subscribing

        local subscribing_worker, channel =
            string.match(
                message,
                "^Worker (%S+): subscribing to channel (.+)$"
            )

        if subscribing_worker ~= nil then

            add_attribute(
                attributes,
                "worker_id",
                subscribing_worker
            )

            add_attribute(
                attributes,
                "channel",
                channel
            )
        end


        -- Worker cleaning registry

        local cleaning_worker, queue =
            string.match(
                message,
                "^Worker (%S+): cleaning registries for queue: (.+)$"
            )

        if cleaning_worker ~= nil then

            add_attribute(
                attributes,
                "worker_id",
                cleaning_worker
            )

            add_attribute(
                attributes,
                "queue",
                queue
            )
        end


        -- Listening on queues

        local queues =
            string.match(
                message,
                "^%*%*%* Listening on (.+)$"
            )

        if queues ~= nil then

            add_attribute(
                attributes,
                "queues",
                queues
            )
        end


        -- Shutdown signal

        local shutdown_signal =
            string.match(
                message,
                "^Received (.+), shutting down"
            )

        if shutdown_signal ~= nil then

            add_attribute(
                attributes,
                "signal",
                shutdown_signal
            )
        end


        -- Shutdown worker

        local shutdown_worker =
            string.match(
                message,
                "^Sent shutdown command to worker with (%d+)"
            )

        if shutdown_worker ~= nil then

            add_attribute(
                attributes,
                "pid",
                shutdown_worker
            )
        end
    end


    -- Build Datum request

    record["body"] = build_body(
        ts,
        level,
        message,
        service,
        source,
        attributes
    )


    record["headers"] = {
        ["Content-Type"] = "application/json"
    }


    return 1, timestamp, record
end