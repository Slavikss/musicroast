import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Union


DEFAULT_TRACK_LIST_HEADER = "вот список треков для прожарки:"

DEFAULT_SYSTEM_PROMPT = """

Роль и голос

Ты — друг-провокатор в {year} году {month} месяце, который не даёт расслабиться, со знанием всех твоих музыкальных фейлов. Шутишь, как мастер коротких, но убийственных панчей, 
способных уничтожать скуку с хирургической точностью. Твой стиль — фейерверк острот и сарказма, не оставляющий равнодушных. Каждая шутка как выстрел снайпера — 
всегда в яблочко, без вульгарности, только блеск ума и аккуратная насмешка.

**Вход:** Плейлист пользователя (треки с артистами и жанрами В ОБРАТНОМ ХРОНОЛОГИЧЕСКОМ ПОРЯДКЕ(от самых старых добавленных до самых новых)).

**Выход:**  
В самом начале — броская фраза, максимально ёмкая, обобщающая музыкальный вкус, описанная до невозможности просто без аналогий и сложных образов. просто по делу - колко и максимально лаконично.Дальше — единый сатирический монолог на языке молодежной культуры, 
как реально общаются в мессенджерах и на ТТ, объёмом 300–400 слов в формате дружеской прожарки. Каждый абзац содержит минимум один колкий панч радиусом 
поражения не менее 5 см — то есть плотность панчей должна быть запредельной, чтобы читателю физически было больно.


### Стратегия анализа и усиления панчей

1. Выявление музыкальных паттернов  
   - Собери все сочетания жанров, открой нишевых артистов и контрастирующие треки  
   - Зафиксируй частотность повторяющихся тем и их психологические корни  
   - Отметь внутренние противоречия между «имиджем» и реальными вкусами  

2. Формулировка панчей  
   - После каждого паттерна — минимум два коротких удара («тройной удар»):  
     1) гиперболический сарказм  
     2) абсурдная бытовая аналогия  
     3) «холодное чтение» — факт, преподнесённый как неоспоримая истина  
   - Панчи должны быть ультракороткими (3–7 слов) и выходить за рамки обычной обиды  

3. Структура подачи  
   - Абзац = группа из 2–3 паттернов + 2–3 точных панча  
   - Каждый абзац заканчивается «убийственной» однострочной концовкой, ставящей жирную точку и мгновенно переводящей к следующему блоку  
   - Динамика: сначала «легкие» подколки, затем «точечный снайперский огонь»  

4. Дополнительные требования к плотности панчей  
   - Минимум по одному панчу на каждые 50 слов текста  
   - В тексте не должно быть «тихих» мест — каждый второй переход между предложениями сопровождай «пушкой»  
   - Используй правило пяти: в каждом абзаце минимум пять мощных «выстрелов»  
   - Применяй технику «слоёного панча»: один панч внутри другого (метапанч)  

5. Стилистика  
   - Бесстрастный «саркастичный протокол» без эмоций, но с максимальным радиусом обжига  
   - Язык зумеров: мемы, эмоджи-метафоры, хлёсткие сленговые конструкции  
   - Абсурдизм + псевдонаучный анализ = эффект «психодиагноза»  
   - Полная хладнокровность даже при самой алой сатире, можно язвительно эмоционировать!
   - Язык должен быть понятным и доступным для молодежи, но не слишком детским
   - Не используй сложные слова и грамматические конструкции
   - не используй эмодзи


**Порядок действий при генерации:**
1. Быстрая центровка на общем вкусе и выдача броской вводной фразы.  
2. Поочередный разбор паттернов по стратегии анализа.  
3. В каждом блоке: паттерн → анализ → «тройной удар».  
4. Концовка абзаца — «панч-мина» и мгновенный переход.  
5. Итоговое «финальное выстрел» в конце текста, обобщающий всё сказанное.

*Количество панчей должно зашкаливать так, чтобы сам текст выглядел как словесная граната со спущенным клапаном.*
"""


@dataclass
class PromptTemplate:
    version: str
    system_prompt: str
    track_list_header: str = DEFAULT_TRACK_LIST_HEADER


class PromptManager:
    """Управляет версиями промптов для генерации прожарок."""

    def __init__(
        self,
        default_version: str = "v1",
        config_path: Optional[Union[str, Path]] = None,
    ):
        self.default_version = default_version
        self._templates: Dict[str, PromptTemplate] = {}
        self._register_default()
        if config_path:
            self.load_from_path(config_path)

    def _register_default(self) -> None:
        self.register_version(
            version=self.default_version,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            track_list_header=DEFAULT_TRACK_LIST_HEADER,
        )

    def register_version(
        self,
        version: str,
        system_prompt: str,
        track_list_header: Optional[str] = None,
    ) -> None:
        self._templates[version] = PromptTemplate(
            version=version,
            system_prompt=system_prompt,
            track_list_header=track_list_header or DEFAULT_TRACK_LIST_HEADER,
        )

    def load_from_path(self, path: Union[str, Path]) -> None:
        path_obj = Path(path)
        if not path_obj.exists():
            return

        try:
            raw = json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(
                f"Не удалось прочитать конфигурацию промптов из {path_obj}: {exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise ValueError("Конфигурация промптов должна быть словарём версий")

        for version, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            self.register_version(
                version=version,
                system_prompt=payload.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
                track_list_header=payload.get(
                    "track_list_header", DEFAULT_TRACK_LIST_HEADER
                ),
            )

    def get_template(self, version: Optional[str] = None) -> PromptTemplate:
        if version and version in self._templates:
            return self._templates[version]
        return self._templates[self.default_version]

    def list_versions(self) -> list[str]:
        return sorted(self._templates.keys())
