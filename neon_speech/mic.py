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

import audioop
from time import sleep, time as get_time

from mycroft.audio import is_speaking, wait_while_speaking
from mycroft.client.speech.hotword_factory import HotWordEngine
from mycroft.client.speech.mic import get_silence, \
    ResponsiveRecognizer as MycroftResponsiveRecognizer
from mycroft.util import play_ogg, play_wav, play_mp3, resolve_resource_file
from mycroft.util.log import LOG
from speech_recognition import (
    AudioSource,
    AudioData
)


class ResponsiveRecognizer(MycroftResponsiveRecognizer):
    def __init__(self, loop, *args, **kwargs):
        self.loop = loop
        # dummy to allow subclassing
        wake_word_recognizer = HotWordEngine("dummy")
        super().__init__(wake_word_recognizer, *args, **kwargs)

        listener_config = self.config.get('listener')

        # The minimum seconds of silence required at the end
        # before a phrase will be considered complete
        self.min_silence_at_end = listener_config.get(
            "min_silence_at_end", 0.25)
        # The minimum seconds of noise before a
        # phrase can be considered complete
        self.min_loud_sec_per_phrase = listener_config.get(
            "min_loud_sec_per_phrase", 0.5)
        # Time between checks for the wake word
        self.sec_between_ww_checks = listener_config.get(
            "sec_between_ww_checks", 0.2)
        # if saving utterances, include wake word ?
        self.include_wuw_in_utterance = listener_config.get(
            "include_wuw_in_utterance", False)
        # The maximum audio in seconds to keep for transcribing a phrase
        # The wake word must fit in this time
        num_phonemes = 10
        len_phoneme = listener_config.get('phoneme_duration', 120) / 1000.0
        self.test_ww_sec = num_phonemes * len_phoneme
        self.saved_ww_sec = max(3, self.test_ww_sec)

        self.listen_requested = False
        self.audio_consumers = None

    def bind(self, audio_consumers):
        self.audio_consumers = audio_consumers

    def feed_hotwords(self, chunk):
        """ feed sound chunk to hotword engines that perform
         streaming predictions (eg, precise) """
        for ww, hotword in self.loop.engines.items():
            hotword["engine"].update(chunk)

    @staticmethod
    def sec_to_bytes(sec, source):
        return int(sec * source.SAMPLE_RATE) * source.SAMPLE_WIDTH

    def check_for_hotwords(self, audio_data):
        # check hot word
        for ww, hotword in self.loop.engines.items():
            if hotword.get("wakeup"):
                # ignore sleep mode hotword
                continue
            if hotword["engine"].found_wake_word(audio_data):
                yield ww

    def trigger_listen(self):
        """Externally trigger listening."""
        LOG.debug('Listen triggered from external source.')
        self.listen_requested = True

    def record_sound_chunk(self, source):
        chunk = source.stream.read(source.CHUNK, self.overflow_exc)
        self.audio_consumers.feed_speech(self._create_audio_data(chunk,
                                                             source))
        return chunk

    def _skip_wake_word(self):
        """Check if told programatically to skip the wake word

        For example when we are in a dialog with the user.
        """
        if not self.loop.use_wake_words:
            return True
        # Check if told programmatically to skip the wake word
        if self.listen_requested:
            self.listen_requested = False
            return True
        try:
            # handles ipc signals for button press
            return super(ChatterboxResponsiveRecognizer, self)._skip_wake_word()
        except:
            # probably permission issues or misconfigured ipc
            # signals are discouraged, button press should emit a proper bus
            # message instead
            return False

    def _wait_until_wake_word(self, source, sec_per_buffer):
        """Listen continuously on source until a wake word is spoken

        Args:
            source (AudioSource):  Source producing the audio chunks
            sec_per_buffer (float):  Fractional number of seconds in each chunk
        """
        num_silent_bytes = int(self.SILENCE_SEC * source.SAMPLE_RATE *
                               source.SAMPLE_WIDTH)

        silence = get_silence(num_silent_bytes)

        # bytearray to store audio in
        byte_data = silence

        buffers_per_check = self.sec_between_ww_checks / sec_per_buffer
        buffers_since_check = 0.0

        # Max bytes for byte_data before audio is removed from the front
        max_size = self.sec_to_bytes(self.saved_ww_sec, source)
        test_size = self.sec_to_bytes(self.test_ww_sec, source)

        said_wake_word = False

        # Rolling buffer to track the audio energy (loudness) heard on
        # the source recently.  An average audio energy is maintained
        # based on these levels.
        energies = []
        idx_energy = 0
        avg_energy = 0.0
        energy_avg_samples = int(5 / sec_per_buffer)  # avg over last 5 secs
        counter = 0
        end_seconds = 0
        while not said_wake_word and not self._stop_signaled:
            if self._skip_wake_word():
                return byte_data, self.config.get("lang", "en-us")
            chunk = self.record_sound_chunk(source)
            self.audio_consumers.feed_audio(self._create_audio_data(chunk,
                                                                    source))
            energy = self.calc_energy(chunk, source.SAMPLE_WIDTH)
            if energy < self.energy_threshold * self.multiplier:
                self._adjust_threshold(energy, sec_per_buffer)

            if len(energies) < energy_avg_samples:
                # build the average
                energies.append(energy)
                avg_energy += float(energy) / energy_avg_samples
            else:
                # maintain the running average and rolling buffer
                avg_energy -= float(energies[idx_energy]) / energy_avg_samples
                avg_energy += float(energy) / energy_avg_samples
                energies[idx_energy] = energy
                idx_energy = (idx_energy + 1) % energy_avg_samples

                # maintain the threshold using average
                if energy < avg_energy * 1.5:
                    if energy > self.energy_threshold:
                        # bump the threshold to just above this value
                        self.energy_threshold = energy * 1.2

            # Periodically output energy level stats.  This can be used to
            # visualize the microphone input, e.g. a needle on a meter.
            if counter % 3:
                try:
                    with open(self.mic_level_file, 'w') as f:
                        f.write("Energy:  cur=" + str(energy) + " thresh=" +
                                str(self.energy_threshold))
                except Exception as e:
                    LOG.warning("Could not save mic level to ipc directory")
                    LOG.error(e)
            counter += 1

            # At first, the buffer is empty and must fill up.  After that
            # just drop the first chunk bytes to keep it the same size.
            needs_to_grow = len(byte_data) < max_size
            if needs_to_grow:
                byte_data += chunk
            else:  # Remove beginning of audio and add new chunk to end
                byte_data = byte_data[len(chunk):] + chunk

            buffers_since_check += 1.0
            self.feed_hotwords(chunk)
            if buffers_since_check > buffers_per_check:
                end_seconds += self.sec_between_ww_checks
                buffers_since_check -= buffers_per_check
                chopped = byte_data[-test_size:] \
                    if test_size < len(byte_data) else byte_data
                audio_data = chopped + silence
                said_hot_word = False
                for hotword in self.check_for_hotwords(audio_data):
                    said_hot_word = True
                    engine = self.loop.engines[hotword]["engine"]
                    sound = self.loop.engines[hotword]["sound"]
                    utterance = self.loop.engines[hotword]["utterance"]
                    listen = self.loop.engines[hotword]["listen"]
                    stt_lang = self.loop.engines[hotword]["stt_lang"]
                    LOG.info("Hot Word: " + hotword)
                    # If enabled, play a wave file with a short sound to audibly
                    # indicate hotword was detected.
                    if sound:
                        try:
                            audio_file = resolve_resource_file(sound)
                            if audio_file:
                                source.mute()
                                if audio_file.endswith(".wav"):
                                    play_wav(audio_file).wait()
                                elif audio_file.endswith(".mp3"):
                                    play_mp3(audio_file).wait()
                                elif audio_file.endswith(".ogg"):
                                    play_ogg(audio_file).wait()
                                source.unmute()
                            else:
                                LOG.error(f"could not find audio file: {sound}")
                        except Exception as e:
                            LOG.warning(e)

                    # Hot Word succeeded
                    payload = {
                        'hotword': hotword,
                        'start_listening': listen,
                        'sound': sound,
                        'utterance': utterance,
                        'stt_lang': stt_lang,
                        "engine": engine.__class__.__name__
                    }

                    if self.save_wake_words:
                        filename = join(self.saved_wake_words_dir,
                                        hotword + "_" + str(
                                            get_time()) + ".wav")
                        payload["filename"] = filename
                        LOG.info("Saving wake word locally: " + filename)
                        # Save wake word locally
                        # TODO

                    self.loop.emit("recognizer_loop:hotword", payload)

                    if utterance:
                        LOG.debug("Hotword utterance: " + utterance)
                        # send the transcribed word on for processing
                        payload = {
                            'utterances': [utterance],
                            "lang": stt_lang
                        }
                        self.loop.emit("recognizer_loop:utterance", payload)

                    if listen:
                        return byte_data, stt_lang

                if said_hot_word:
                    self.audio_consumers.feed_hotword(
                        self._create_audio_data(byte_data, source))
                    # reset bytearray to store wake word audio in, else many
                    # serial detections
                    byte_data = silence

    def listen(self, source, stream):
        """Listens for chunks of audio that Mycroft should perform STT on.

        This will listen continuously for a wake-up-word, then return the
        audio chunk containing the spoken phrase that comes immediately
        afterwards.

        Args:
            source (AudioSource):  Source producing the audio chunks
            stream (AudioStreamHandler): Stream target that will receive chunks
                                         of the utterance audio while it is
                                         being recorded

        Returns:
            (AudioData, lang): audio with the user's utterance (minus the
                               wake-up-word), stt_lang
        """
        assert isinstance(source, AudioSource), "Source must be an AudioSource"

        #        bytes_per_sec = source.SAMPLE_RATE * source.SAMPLE_WIDTH
        sec_per_buffer = float(source.CHUNK) / source.SAMPLE_RATE

        # Every time a new 'listen()' request begins, reset the threshold
        # used for silence detection.  This is as good of a reset point as
        # any, as we expect the user and Mycroft to not be talking.
        # NOTE: adjust_for_ambient_noise() doc claims it will stop early if
        #       stt is detected, but there is no code to actually do that.
        self.adjust_for_ambient_noise(source, 1.0)
        # If skipping wake words, just pass audio to our streaming STT
        # TODO: Check config updates?
        if stream and not self.loop.use_wake_words:
            stream.stream_start()
            frame_data = get_silence(source.SAMPLE_WIDTH)
            LOG.debug("Stream starting!")
            # a crude way to detect the end of an utterance is not detecting
            # changes for N times in a row, this gives us support for all
            # streaming engines, ideally there would be a proper event
            # TODO add a check and support for this, standardize api in ovos
            prev_transcript = ""
            count = 0
            while counter <= 5:  # TODO config for thresh ?

                # stream audio until stable STT transcript detected
                # (this is called again immediately)
                chunk = self.record_sound_chunk(source)

                # Filter out TTS
                if not is_speaking():
                    stream.stream_chunk(chunk)
                    frame_data += chunk
                else:
                    # if TTS started discard old audio
                    frame_data = get_silence(source.SAMPLE_WIDTH)
                    wait_while_speaking()
                    break

                if prev_transcript and not stream.text:
                    break # stt reset transcription internally
                elif stream.text and stream.text != prev_transcript:
                    prev_transcript = stream.text # transcript updated
                else:
                    counter += 1  # transcript stabilizing

            LOG.debug("stream ended!")
        # If using wake words, wait until the wake_word is detected and then record the following phrase
        else:
            if self.loop.use_wake_words:
                LOG.debug("Waiting for wake word...")
                wuw_frame_data, lang = self._wait_until_wake_word(source,
                                                                  sec_per_buffer)
                self.audio_consumers.feed_hotword(
                    self._create_audio_data(wuw_frame_data, source))

            # abort recording, eg, button press
            if self._stop_signaled:
                return

            LOG.debug("Recording...")
            self.loop.emit("recognizer_loop:record_begin")

            frame_data = self._record_phrase(
                source,
                sec_per_buffer,
                stream
            )
            if self.include_wuw_in_utterance and wuw_frame_data is not None:
                frame_data = wuw_frame_data + frame_data

        audio_data = self._create_audio_data(frame_data, source)
        self.loop.emit("recognizer_loop:record_end")

        if self.save_utterances:
            LOG.info("Saving Utterance Recording")
            pass  # TODO

        return audio_data, lang

