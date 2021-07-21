# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
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
