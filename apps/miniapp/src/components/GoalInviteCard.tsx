/**
 * Приглашение в анкету цели на домашнем экране клиента.
 *
 * Решение владельца 30.08: человек, открывший мини-апп, обязан встретить
 * анкету цели. До этого единственный вход в `/customer/goal-select` жил
 * в стартовой сетке БОТА — то есть в переписке, — и тот, кто пришёл
 * ссылкой в приложение, анкету не встречал никогда.
 *
 * # Кто «новый» — решает сервер, не экран
 *
 * `GET /decision-context` уже отвечает на этот вопрос: поле `missing` —
 * «чего не хватает». Приглашение показывается ровно тогда, когда
 * `missing` непуст, и не показывается, когда пуст. Никакой эвристики
 * «нет записей — значит новый»: она разошлась бы с сервером в первый же
 * день (человек без записей, но с целью — не новый; человек с записью,
 * но без цели — цель всё равно не выбрал).
 *
 * Экран не интерпретирует и `kind`: `goal`, `goal_clarification`,
 * `goal_guidance` рисуются одинаково — текстом `prompt` как есть. Тот же
 * инвариант, что и у `GoalSelectScreen`: документ отображается,
 * решения не принимаются.
 *
 * # Что здесь НЕ обещается
 *
 * `GOAL_RESOLUTION_ENABLED` на пилоте выключен — выбранная цель пока ни
 * на что не влияет. Поэтому в карточке нет ни слова про подбор,
 * рекомендации и «подберём под твою цель»: единственный текст — вопрос,
 * который прислал сервер, и подпись кнопки, называющая место назначения
 * («Выбрать цель» — та же формулировка, что у кнопки бота). Вопрос
 * останется правдой и после включения флага; обещание — стало бы ложью
 * до него.
 *
 * # Дорога к записи
 *
 * Карточка — не модальное окно и не шаг мастера: обычная секция ПОД
 * содержимым экрана. Табы, список записей, нижняя навигация и «Найти
 * услугу» остаются на месте, работают и идут первыми. Порядок здесь
 * нормативный: BOT-001 §13 — «First Contact MUST NOT begin with a
 * standalone questionnaire», а Mini App entry входит в область
 * действия BOT-001 (§2.1). У нового человека список записей пуст, так
 * что приглашение всё равно видно на первом экране без прокрутки.
 *
 * Сбой `decision-context` не рисует ничего — домашний экран не имеет
 * права падать из-за необязательного приглашения.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";

export function GoalInviteCard() {
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DecisionContext | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDecisionContext()
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch(() => {
        /* приглашение необязательно — молчим, экран остаётся рабочим */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!doc || doc.missing.length === 0) return null;

  return (
    <section className="goal-invite" aria-label="Цель">
      {doc.missing.map((item, index) => (
        <p key={`${item.kind}-${index}`} className="goal-invite__prompt">
          {item.prompt}
        </p>
      ))}
      <div className="goal-invite__actions">
        <button
          type="button"
          className="btn-secondary goal-invite__cta"
          onClick={() => navigate("/customer/goal-select")}
        >
          Выбрать цель
        </button>
      </div>
    </section>
  );
}
