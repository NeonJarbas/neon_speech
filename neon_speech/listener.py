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

import time
from queue import Queue, Empty
from threading import Thread
from time import sleep

import pyaudio
from mycroft.client.speech.listener import AudioStreamHandler, \
    AudioProducer as MycroftAudioProducer, AudioConsumer as \
    MycroftAudioConsumer, RecognizerLoopState, recognizer_conf_hash, \
    RecognizerLoop as MycroftRecognizerLoop, MAX_MIC_RESTARTS, AUDIO_DATA, \
    STREAM_START, STREAM_DATA, STREAM_STOP
from mycroft.configuration import Configuration
from mycroft.tts.cache import hash_sentence
from mycroft.util.log import LOG
from neon_speech.hotword_factory import HotWordFactory
from neon_speech.mic import MutableMicrophone, ResponsiveRecognizer
from neon_speech.stt import STTFactory
from neon_speech.utils import find_input_device
from ovos_utils.json_helper import merge_dict


class AudioProducer(MycroftAudioProducer):
    def __init__(self, loop):
        Thread.__init__(self)
        self.daemon = True
        self.loop = loop
        self.stream_handler = None
        if self.loop.stt.can_stream:
            self.stream_handler = AudioStreamHandler(self.loop.queue)

    @property
    def microphone(self):
        return self.loop.microphone

    @property
    def recognizer(self):
        return self.loop.responsive_recognizer

    def run(self):
        restart_attempts = 0
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source)
            while self.loop.state.running:
                try:
                    audio, context = self.recognizer.listen(source,
                                                            self.stream_handler)
                    if audio is not None:
                        audio, metadata = \
                            self.recognizer.audio_consumers.get_context(audio)
                        context = merge_dict(context, metadata)
                        self.loop.queue.put((AUDIO_DATA, audio, context))
                    else:
                        LOG.warning("Audio contains no data.")
                except IOError as e:
                    # IOError will be thrown if the read is unsuccessful.
                    # If self.recognizer.overflow_exc is False (default)
                    # input buffer overflow IOErrors due to not consuming the
                    # buffers quickly enough will be silently ignored.
                    LOG.exception('IOError Exception in AudioProducer')
                    if e.errno == pyaudio.paInputOverflowed:
                        pass  # Ignore overflow errors
                    elif restart_attempts < MAX_MIC_RESTARTS:
                        # restart the mic
                        restart_attempts += 1
                        LOG.info('Restarting the microphone...')
                        source.restart()
                        LOG.info('Restarted...')
                    else:
                        LOG.error('Restarting mic doesn\'t seem to work. '
                                  'Stopping...')
                        raise
                except Exception:
                    LOG.exception('Exception in AudioProducer')
                    raise
                else:
                    # Reset restart attempt counter on sucessful audio read
                    restart_attempts = 0
                finally:
                    if self.stream_handler is not None:
                        self.stream_handler.stream_stop()

    def stop(self):
        """Stop producer thread."""
        self.loop.state.running = False
        self.loop.responsive_recognizer.stop()


class AudioConsumer(MycroftAudioConsumer):
    def __init__(self, loop):
        Thread.__init__(self)
        self.daemon = True
        self.loop = loop

    @property
    def wakeup_engines(self):
        """ wake from sleep mode """
        return [(ww, w["engine"]) for ww, w in self.loop.engines.items()
                if w["wakeup"]]

    def run(self):
        while self.loop.state.running:
            self.read()

    def read(self):
        try:
            message = self.loop.queue.get(timeout=0.5)
        except Empty:
            return

        if message is None:
            return

        tag, data, context = message
        lang = context.get("lang") or self.loop.stt.lang
        if tag == AUDIO_DATA:
            if data is not None:
                if self.loop.state.sleeping:
                    self.wake_up(data)
                else:
                    self.process(data, context)
        elif tag == STREAM_START:
            # TODO stream_start doesnt do anything with lang param ?
            self.loop.stt.stream_start(lang)
        elif tag == STREAM_DATA:
            self.loop.stt.stream_data(data)
        elif tag == STREAM_STOP:
            self.loop.stt.stream_stop()
        else:
            LOG.error("Unknown audio queue type %r" % message)

    def wake_up(self, audio):
        for ww, wakeup_recognizer in self.wakeup_engines:
            if wakeup_recognizer.found_wake_word(audio.frame_data):
                self.loop.state.sleeping = False
                self.loop.emit('recognizer_loop:awoken')
                break

    def _get_lang(self, context):
        user = context.get("user")
        if self.chat_user_database:
            # TODO this needs to be revisited once a unified user db is
            #  introduced, right now this only comes from Klat, in the
            #  future mycroft will be locally aware of users and the same
            #  code should work for both cases
            # self.server_listener.get_nick_profiles(flac_filename)
            self.loop.chat_user_database.update_profile_for_nick(user)
            chat_user = self.loop.chat_user_database.get_profile(user)
            stt_language = chat_user["speech"].get('stt_language', 'en')
            alt_langs = chat_user["speech"].get("alt_languages", ['en', 'es'])
        else:
            # context might contain language from wake-word or from some
            # audio module (eg, speaker identification)
            stt_language = context.get("lang")
            alt_langs = None
        return stt_language or self.loop.stt.lang

    def process(self, audio, context=None):
        if audio is None:
            return
        context = context or {}
        lang = context.get("lang") or self.loop.stt.lang
        heard_time = time.time()
        if self._audio_length(audio) < self.MIN_AUDIO_SIZE:
            LOG.warning("Audio too short to be processed")
        else:
            transcription = self.transcribe(audio, lang)
            transcribed_time = time.time()
            if transcription:
                ident = str(time.time()) + hash_sentence(transcription)
                # STT succeeded, send the transcribed stt on for processing
                payload = {
                    'utterances': [transcription],
                    'lang': lang,
                    'ident': ident,
                    "data": context,
                    "raw_audio": context.get("audio_filename"),
                    "timing": {"start": heard_time,
                               "transcribed": transcribed_time}
                }
                self.loop.emit("recognizer_loop:utterance", payload)

    def send_stt_failure_event(self):
        """ Send message that nothing was transcribed. """
        if self.loop.use_wake_words:  # Don't capture ambient noise
            self.loop.emit('recognizer_loop:stt.recognition.unknown')

    def transcribe(self, audio, lang=None):
        try:
            # Invoke the STT engine on the audio clip
            text = self.loop.stt.execute(audio, language=lang) or ""
            if text:
                LOG.debug("STT: " + text)
            else:
                LOG.info('no words were transcribed')
                self.send_stt_failure_event()
            return text.strip()
        except Exception as e:
            self.send_stt_failure_event()
            LOG.error(e)
            LOG.error("Speech Recognition could not understand audio")
            return None


class RecognizerLoop(MycroftRecognizerLoop):
    """ EventEmitter loop running speech recognition.

    Local wake word recognizer and remote general speech recognition.
    """

    def __init__(self, bus, *args, **kwargs):
        self.bus = bus
        self.engines = {}
        self.stt = None
        self.fallback_stt = None
        self.queue = None
        self.audio_consumer = None
        self.audio_producer = None
        self.responsive_recognizer = None
        self.use_wake_words = True
        try:
            from NGI.server.chat_user_database import KlatUserDatabase
            self.chat_user_database = KlatUserDatabase()
        except Exception as e:
            self.chat_user_database = None
        super().__init__(*args, **kwargs)

    def _load_config(self):
        """Load configuration parameters from configuration."""
        config = Configuration.get()
        self.config_core = config
        self._config_hash = recognizer_conf_hash(config)
        self.lang = config.get('lang') or "en-us"
        self.config = config.get('listener') or {}
        rate = self.config.get('sample_rate')

        device_index = self.config.get('device_index')
        device_name = self.config.get('device_name')
        if not device_index and device_name:
            device_index = find_input_device(device_name)
        LOG.debug('Using microphone (None = default): ' + str(device_index))
        self.microphone = MutableMicrophone(device_index, rate,
                                            mute=self.mute_calls > 0)

        self.create_hotword_engines()
        self.state = RecognizerLoopState()
        self.responsive_recognizer = ResponsiveRecognizer(self)
        self.use_wake_words = self.config.get("wake_word_enabled", True)

    def bind(self, parsers_service):
        self.responsive_recognizer.bind(parsers_service)

    def create_hotword_engines(self):
        LOG.info("creating hotword engines")
        hot_words = self.config_core.get("hotwords", {})
        for word in hot_words:
            try:
                data = hot_words[word]
                sound = data.get("sound")
                utterance = data.get("utterance")
                listen = data.get("listen", False)
                wakeup = data.get("wake_up", False)
                trigger = data.get("trigger", False)
                lang = data.get("stt_lang", self.lang)
                enabled = data.get("active", True)
                if not enabled:
                    continue
                engine = HotWordFactory.create_hotword(word,
                                                       lang=lang,
                                                       loop=self)
                if engine is not None:
                    if hasattr(engine, "bind"):
                        engine.bind(self.bus)
                        # not all plugins implement this
                    self.engines[word] = {"engine": engine,
                                          "sound": sound,
                                          "trigger": trigger,
                                          "utterance": utterance,
                                          "stt_lang": lang,
                                          "listen": listen,
                                          "wakeup": wakeup}
            except Exception as e:
                LOG.error("Failed to load hotword: " + word)

    def start_async(self):
        """Start consumer and producer threads."""
        self.state.running = True
        self.stt = STTFactory.create()
        self.queue = Queue()
        self.audio_consumer = AudioConsumer(self)
        self.audio_consumer.start()
        self.audio_producer = AudioProducer(self)
        self.audio_producer.start()

    def stop(self):
        self.state.running = False
        self.audio_producer.stop()
        # stop wake word detectors
        for ww, hotword in self.engines.items():
            hotword["engine"].stop()
        # wait for threads to shutdown
        self.audio_producer.join()
        self.audio_consumer.join()

    def run(self):
        """Start and reload mic and STT handling threads as needed.

        Wait for KeyboardInterrupt and shutdown cleanly.
        """
        try:
            self.start_async()
        except Exception:
            LOG.exception('Starting producer/consumer threads for listener '
                          'failed.')
            return

        # Handle reload of consumer / producer if config changes
        while self.state.running:
            try:
                sleep(5)
                current_hash = recognizer_conf_hash(
                    Configuration.load_config_stack())
                if current_hash != self._config_hash:
                    self._config_hash = current_hash
                    LOG.debug('Config has changed, reloading...')
                    self.reload()
            except KeyboardInterrupt as e:
                LOG.error(e)
                self.stop()
                raise  # Re-raise KeyboardInterrupt
            except Exception:
                LOG.exception('Exception in RecognizerLoop')

    def reload(self):
        """Reload configuration and restart consumer and producer."""
        self.stop()
        # load config
        self._load_config()
        # restart
        self.start_async()

    def change_wake_word_state(self, enabled: bool):
        self.use_wake_words = enabled
