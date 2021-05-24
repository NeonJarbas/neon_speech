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
import re

import pyaudio
from mycroft.util.log import LOG


def find_input_device(device_name):
    """ Find audio input device by name.

        Arguments:
            device_name: device name or regex pattern to match

        Returns: device_index (int) or None if device wasn't found
    """
    LOG.info('Searching for input device: {}'.format(device_name))
    LOG.debug('Devices: ')
    pa = pyaudio.PyAudio()
    pattern = re.compile(device_name)
    for device_index in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(device_index)
        LOG.debug('   {}'.format(dev['name']))
        if dev['maxInputChannels'] > 0 and pattern.match(dev['name']):
            LOG.debug('    ^-- matched')
            return device_index
    return None


def get_audio_file_stream(wav_file: str, sample_rate: int = 16000):
    """
    Creates a FileStream object for the specified wav_file with the specified output sample_rate.
    Args:
        wav_file: Path to file to read
        sample_rate: Desired output sample rate (None for wav_file sample rate)

    Returns:
        FileStream object for the passed audio file
    """
    class FileStream:
        MIN_S_TO_DEBUG = 5.0

        # How long between printing debug info to screen
        UPDATE_INTERVAL_S = 1.0

        def __init__(self, file_name):
            self.file = get_file_as_wav(file_name, sample_rate)
            self.sample_rate = self.file.getframerate()
            # if sample_rate and self.sample_rate != sample_rate:
            #     sound = AudioSegment.from_file(file_name, format='wav', frame_rate=self.sample_rate)
            #     sound = sound.set_frame_rate(sample_rate)
            #     _, tempfile = mkstemp()
            #     sound.export(tempfile, format='wav')
            #     self.file = wave.open(tempfile, 'rb')
            #     self.sample_rate = self.file.getframerate()
            self.size = self.file.getnframes()
            self.sample_width = self.file.getsampwidth()
            self.last_update_time = 0.0

            self.total_s = self.size / self.sample_rate / self.sample_width

        def calc_progress(self):
            return float(self.file.tell()) / self.size

        def read(self, chunk_size):

            progress = self.calc_progress()
            if progress == 1.0:
                raise EOFError

            return self.file.readframes(chunk_size)

        def close(self):
            self.file.close()

    if not os.path.isfile(wav_file):
        raise FileNotFoundError
    try:
        return FileStream(wav_file)
    except Exception as e:
        raise e
