# NEON AI (TM) SOFTWARE, Software Development Kit & Application Development System
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2021 Neongecko.com Inc.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
# following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions
#    and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions
#    and the following disclaimer in the documentation and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote
#    products derived from this software without specific prior written permission.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
import neon_speech
import os.path
import time
from threading import Lock
from typing import Optional

from mycroft.configuration import Configuration
from mycroft.util import reset_sigint_handler, create_daemon, \
    wait_for_exit_signal
from mycroft.util.log import LOG
from mycroft_bus_client import MessageBusClient
from neon_speech.listener import RecognizerLoop
from neon_speech.plugins import AudioParsersService
from neon_speech.stt import STTFactory
from neon_speech.utils import get_audio_file_stream
from ovos_utils import create_daemon, wait_for_exit_signal
from ovos_utils.json_helper import merge_dict
from ovos_utils.messagebus import Message, get_mycroft_bus
from pydub import AudioSegment
from speech_recognition import AudioData

bus: Optional[MessageBusClient] = None  # Mycroft messagebus connection
loop: Optional[RecognizerLoop] = None
config: Optional[dict] = None
external_stt = None
service = None


def handle_record_begin():
    """Forward internal bus message to external bus."""
    LOG.info("Begin Recording...")
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('recognizer_loop:record_begin', context=context))


def handle_record_end():
    """Forward internal bus message to external bus."""
    LOG.info("End Recording...")
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('recognizer_loop:record_end', context=context))


def handle_no_internet():
    LOG.debug("Notifying enclosure of no internet connection")
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('enclosure.notify.no_internet', context=context))


def handle_awoken():
    """Forward mycroft.awoken to the messagebus."""
    LOG.info("Listener is now Awake: ")
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('mycroft.awoken', context=context))


def handle_utterance(event):
    LOG.info("Utterance: " + str(event['utterances']))
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'raw_audio': event.pop('raw_audio'),
               'destination': ["skills"],
               "timing": event.pop("timing", {})}
    if "data" in event:
        data = event.pop("data")
        context = merge_dict(context, data)
    if 'ident' in event:
        ident = event.pop('ident')
        context['ident'] = ident
    bus.emit(Message('recognizer_loop:utterance', event, context))


def handle_wake_words_state(message):
    enabled = message.data.get("enabled", True)
    loop.change_wake_word_state(enabled)


def handle_hotword(event):
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    if not event.get("listen", False):
        LOG.info("Hotword Detected: " + event['hotword'])
        bus.emit(Message('recognizer_loop:hotword', event, context))
    else:
        LOG.info("Wakeword Detected: " + event['hotword'])
        bus.emit(Message('recognizer_loop:wakeword', event, context))


def handle_unknown():
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('mycroft.speech.recognition.unknown', context=context))


def handle_speak(event):
    """
        Forward speak message to message bus.
    """
    context = {'client_name': 'neon_speech',
               'source': 'audio',
               'destination': ["skills"]}
    bus.emit(Message('speak', event, context))


def handle_complete_intent_failure(message: Message):
    """Extreme backup for answering completely unhandled intent requests."""
    LOG.info("Failed to find intent.")
    bus.emit(message.forward("complete.intent.failure", message.data))


def handle_sleep(message: Message):
    """Put the recognizer loop to sleep."""
    loop.sleep()


def handle_wake_up(message: Message):
    """Wake up the the recognize loop."""
    loop.awaken()


def handle_mic_mute(message: Message):
    """Mute the listener system."""
    loop.mute()


def handle_mic_unmute(message: Message):
    """Unmute the listener system."""
    loop.unmute()


def handle_mic_listen(message: Message):
    """Handler for mycroft.mic.listen.

    Starts listening as if wakeword was spoken.
    """
    loop.responsive_recognizer.trigger_listen()


def handle_mic_get_status(message: Message):
    """Query microphone mute status."""
    data = {'muted': loop.is_muted()}
    message = event.response(data)
    message.context = {'client_name': 'neon_speech',
                       'source': 'audio',
                       'destination': ["skills"]}
    bus.emit(message)


def handle_audio_start(message: Message):
    """Mute recognizer loop."""
    if config.get("listener").get("mute_during_output"):
        loop.mute()


def handle_audio_end(message: Message):
    """Request unmute, if more sources have requested the mic to be muted
    it will remain muted.
    """
    if config.get("listener").get("mute_during_output"):
        loop.unmute()  # restore


def handle_stop(message: Message):
    """Handler for mycroft.stop, i.e. button press."""
    loop.force_unmute()


class ExternalSTTService:
    def __init__(self, bus):
        self.lock = Lock()
        self.bus = bus
        self.stt = STTFactory.create(config=config)
        # Register API Handlers
        self.bus.on("neon.get_stt", self.handle_get_stt)
        self.bus.on("neon.audio_input", self.handle_audio_input)
        self.bus.on('recognizer_loop:klat_utterance',
                    self.handle_input_from_klat)  # TODO: Depreciate and move to server module DM

    # TODO: Depreciate this method
    def handle_input_from_klat(self, message):
        """
        Handles an input from the klat server
        """
        audio_file = message.data.get("raw_audio")
        nick = message.data.get("user")
        loop.chat_user_database.update_profile_for_nick(nick)
        chat_user = loop.chat_user_database.get_profile(nick)
        stt_language = chat_user["speech"].get('stt_language', 'en')
        request_id = f"sid-{message.data.get('sid')}-{message.data.get('socketIdEncrypted')}-" \
                     f"{nick}-{message.data.get('nano')}"  # Formerly known as 'flac_filename'

        try:
            nick_profiles = loop.chat_user_database.get_nick_profiles(
                message.data.get("cid_nicks"))
        except TypeError:
            nick_profiles = loop.chat_user_database.get_nick_profiles(
                [nick])
        mobile = message.data.get("nano") == "mobile"
        if mobile:
            client = "mobile"
        elif message.data.get("nano") == "true":
            client = "nano"
        else:
            client = "klat"
        ident = time.time()

        if audio_file:
            try:
                audio_data, audio_context, transcriptions = \
                    self._get_stt_from_file(audio_file, stt_language)

                if message.data.get("need_transcription"):
                    LOG.debug(f"return stt to server: {transcriptions}")
                    bus.emit(Message("css.emit", {"event": "stt from mycroft",
                                                  "data": [transcriptions[0],
                                                           request_id]}))
            except Exception as x:
                LOG.error(x)
                transcriptions = [message.data.get("shout_text")]
                audio_context = None
        elif message.data.get("need_transcription"):
            LOG.error(f"Need transcription but no audio passed! {message}")
            return
        else:
            audio_context = None
            transcriptions = [message.data.get("shout_text")]

        if not transcriptions:
            LOG.warning(f"Null Transcription!")
            return

        data = {
            "utterances": transcriptions,
            "lang": stt_language
        }
        context = {'client_name': 'mycroft_listener',
                   'source': 'klat',
                   'destination': ["skills"],
                   "audio_parser_data": audio_context,
                   "raw_audio": message.data.get("raw_audio"),
                   "mobile": mobile,  # TODO: Depreciate and use client DM
                   "client": client,  # origin (local, klat, nano, mobile, api)
                   "klat_data": {"cid": message.data.get("cid"),
                                 "sid": message.data.get("sid"),
                                 "title": message.data.get("title"),
                                 "nano": message.data.get("nano"),
                                 "request_id": request_id},
                   # "flac_filename": flac_filename,
                   "neon_should_respond": False,
                   "username": nick,
                   "nick_profiles": nick_profiles,
                   "cc_data": {"speak_execute": transcriptions[0],
                               "raw_utterance": transcriptions[0]},
                   # TODO: Are these necessary anymore? Shouldn't be DM
                   "timing": {"start": message.data.get("time"),
                              "transcribed": time.time()},
                   "ident": ident
                   }
        LOG.debug("Send server request to skills for processing")
        bus.emit(Message('recognizer_loop:utterance', data, context))

    def handle_get_stt(self, message: Message):
        """
        Handles a request for stt. Emits a response to the sender with stt data or error data
        :param message: Message associated with request
        """
        wav_file_path = message.data.get("audio_file")
        lang = message.data.get("lang")
        ident = message.context.get("ident") or "neon.get_stt.response"
        if not wav_file_path:
            bus.emit(message.reply(ident, data={
                "error": f"audio_file not specified!"}))

        if not os.path.isfile(wav_file_path):
            bus.emit(message.reply(ident, data={
                "error": f"{wav_file_path} Not found!"}))

        try:
            _, parser_data, transcriptions = self._get_stt_from_file(
                wav_file_path, lang)
            bus.emit(message.reply(ident, data={"parser_data": parser_data,
                                                "transcripts": transcriptions}))
        except Exception as e:
            LOG.error(e)
            bus.emit(message.reply(ident, data={"error": repr(e)}))

    def handle_audio_input(self, message):
        """
        Handles remote audio input to Neon.
        :param message:
        :return:
        """

        def build_context(msg: Message):
            ctx = {'client_name': 'mycroft_listener',
                   'source': msg.context.get("source" or "speech_api"),
                   'destination': ["skills"],
                   "audio_parser_data": msg.context.get("audio_parser_data"),
                   "client": msg.context.get("client"),
                   # origin (local, klat, nano, mobile, api)
                   "neon_should_respond": msg.context.get(
                       "neon_should_respond"),
                   "username": msg.context.get("username"),
                   "timing": {"start": msg.data.get("time"),
                              "transcribed": time.time()},
                   "ident": msg.context.get("ident", time.time())
                   }
            if msg.context.get("klat_data"):
                ctx["klat_data"] = msg.context("klat_data")
                ctx["nick_profiles"] = msg.context.get("nick_profiles")
            return ctx

        ident = message.context.get("ident") or "neon.audio_input.response"
        wav_file_path = message.data.get("audio_file")
        lang = message.data.get("lang")
        try:
            _, parser_data, transcriptions = self._get_stt_from_file(
                wav_file_path, lang)
            message.context["audio_parser_data"] = parser_data
            context = build_context(message)
            data = {
                "utterances": transcriptions,
                "lang": message.data.get("lang", "en-us")
            }
            handled = True  # TODO
            bus.emit(Message('recognizer_loop:utterance', data, context))
            bus.emit(message.reply(ident, data={"parser_data": parser_data,
                                                "transcripts": transcriptions,
                                                "skills_recv": handled}))
        except Exception as e:
            LOG.error(e)
            bus.emit(message.reply(ident, data={"error": repr(e)}))

    def _get_stt_from_file(self, wav_file: str, lang: str = "en-us") -> (
            AudioData, dict, list):
        """
        Performs STT and audio processing on the specified wav_file
        :param wav_file: wav audio file to process
        :param lang: language of passed audio
        :return: (AudioData of object, extracted context, transcriptions)
        """
        segment = AudioSegment.from_file(wav_file)
        audio_data = AudioData(segment.raw_data, segment.frame_rate,
                               segment.sample_width)
        audio_stream = get_audio_file_stream(wav_file)
        with self.lock:
            if self.stt.can_stream:
                self.stt.stream_start(lang)
                while True:
                    try:
                        data = audio_stream.read(1024)
                        self.stt.stream_data(data)
                    except EOFError:
                        break
                transcriptions = self.stt.stream_stop()
            else:
                transcriptions = self.stt.execute(audio_data, lang)
        audio, audio_context = service.get_context(audio_data)
        return audio, audio_context, transcriptions


def main(speech_config=None):
    global bus
    global loop
    global config
    global service
    global external_stt

    reset_sigint_handler()
    bus = get_mycroft_bus()  # Mycroft messagebus, see mycroft.messagebus
    config = speech_config or Configuration.get()

    # Register handlers on internal RecognizerLoop emitter
    loop = RecognizerLoop(config)
    loop.on('recognizer_loop:utterance', handle_utterance)
    loop.on('recognizer_loop:speech.recognition.unknown', handle_unknown)
    loop.on('speak', handle_speak)
    loop.on('recognizer_loop:record_begin', handle_record_begin)
    loop.on('recognizer_loop:awoken', handle_awoken)
    loop.on('recognizer_loop:hotword', handle_hotword)
    loop.on('recognizer_loop:record_end', handle_record_end)
    loop.on('recognizer_loop:no_internet', handle_no_internet)

    # Register handlers for events on main Mycroft messagebus
    bus.on('complete_intent_failure', handle_complete_intent_failure)
    bus.on('recognizer_loop:sleep', handle_sleep)
    bus.on('recognizer_loop:wake_up', handle_wake_up)
    bus.on('mycroft.mic.mute', handle_mic_mute)
    bus.on('mycroft.mic.unmute', handle_mic_unmute)
    bus.on('mycroft.mic.get_status', handle_mic_get_status)
    bus.on('mycroft.mic.listen', handle_mic_listen)
    bus.on('recognizer_loop:audio_output_start', handle_audio_start)
    bus.on('recognizer_loop:audio_output_end', handle_audio_end)
    bus.on('mycroft.stop', handle_stop)

    # State Change Notifications
    bus.on("neon.wake_words_state", handle_wake_words_state)

    # klat / bus stt requests handler
    external_stt = ExternalSTTService(bus)

    service = AudioParsersService(bus, config=config)
    service.start()
    loop.bind(service)

    create_daemon(loop.run)

    wait_for_exit_signal()


if __name__ == "__main__":
    main()
