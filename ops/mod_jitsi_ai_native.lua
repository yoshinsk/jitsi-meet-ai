-- File: ops/mod_jitsi_ai_native.lua
-- Summary: Authorize AI rooms through the private service before enabling native Jitsi transcription and translation.
local http = require 'net.http';
local json = require 'util.json';
local jid = require 'util.jid';
local is_jibri = module:require('util').is_jibri;
local token_file = assert(io.open('/etc/prosody/jitsi-ai-bridge-token', 'r'));
local bridge_token = token_file:read('*a'):gsub('%s+$', '');
token_file:close();

local function eligible(room)
    local node = jid.node(room.jid);
    return node and node:match('^ai%-%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x$');
end

local function register(room, finished)
    if not eligible(room) or not room._data.meetingId then return; end
    local participants = {};
    for _, occupant in room:each_occupant() do
        local presence = occupant:get_presence();
        local name = presence and presence:get_child_text('nick', 'http://jabber.org/protocol/nick');
        -- Control and hidden recording connections are not meeting speakers.
        if not is_jibri(occupant) and jid.resource(occupant.nick) ~= 'focus' then
            participants[jid.resource(occupant.nick)] = name or jid.resource(occupant.nick);
        end
    end
    http.request('http://127.0.0.1:8765/internal/bridge/register', {
        method = 'POST';
        headers = { ['Content-Type'] = 'application/json'; Authorization = 'Bearer ' .. bridge_token; };
        body = json.encode({room=jid.node(room.jid); meeting_id=room._data.meetingId;
                            participants=participants; finished=finished or false;});
    }, function(body, code)
        if finished then return; end
        local enabled = code == 200;
        room.jitsiMetadata = room.jitsiMetadata or {};
        -- Advertise availability without choosing a listener language: the official client's initial language is null/off.
        -- Subsequent participant updates must preserve a moderator's explicit room-wide disable choice.
        local translation_enabled = enabled and (not room._jitsi_ai_authorized
            or (room.jitsiMetadata.audioTranslation and room.jitsiMetadata.audioTranslation.enabled ~= false));
        room._jitsi_ai_authorized = enabled;
        room.jitsiMetadata.audioTranslationAvailable = enabled;
        room.jitsiMetadata.audioTranslation = {enabled=translation_enabled or false};
        room.jitsiMetadata.asyncTranscription = enabled;
        room.jitsiMetadata.recording = room.jitsiMetadata.recording or {};
        room.jitsiMetadata.recording.isTranscribingEnabled = enabled;
        module:fire_event('room-metadata-changed', {room=room;});
        if not enabled then module:log('warn', 'AI room authorization unavailable (HTTP %s)', tostring(code)); end
    end);
end

module:hook('muc-room-created', function(event)
    -- Default closed: ordinary anonymous rooms cannot consume the administrator's API allowance.
    event.room.jitsiMetadata = event.room.jitsiMetadata or {};
    event.room.jitsiMetadata.audioTranslationAvailable = false;
    event.room.jitsiMetadata.audioTranslation = {enabled=false};
    register(event.room, false);
end, -3);

module:hook('muc-occupant-joined', function(event) register(event.room, false); end, -3);
module:hook('muc-occupant-left', function(event) register(event.room, false); end, -3);
module:hook('muc-room-destroyed', function(event) register(event.room, true); end, -3);
module:hook('jitsi-audio-translation-allow-publish', function(event)
    if not event.room._jitsi_ai_authorized then return false; end
end, 10);
