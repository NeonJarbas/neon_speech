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
from mycroft.client.speech.hotword_factory import HotWordEngine, \
    HotWordFactory as MycroftHotWordFactory
from mycroft.configuration import Configuration


class HotWordFactory(MycroftHotWordFactory):
    MODULE_MAPPINGS = {
        "pocketsphinx": "ovos_ww_pocketsphinx",
        "precise": "ovos_ww_precise"
    }

    @classmethod
    def create_hotword(cls, hotword="dummy", config=None,
                       lang="en-us", loop=None):
        ww_config_core = config or Configuration.get().get("hotwords", {})
        config = ww_config_core.get(hotword) or {}
        module = config.get("module", "dummy_ww_plug")
        if module in HotWordFactory.MODULE_MAPPINGS:
            module = HotWordFactory.MODULE_MAPPINGS[module]
        return cls.load_module(module, hotword, config, lang, loop) or \
               HotWordEngine("dummy")
