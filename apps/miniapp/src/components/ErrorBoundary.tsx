/**
 * Граница ошибок вокруг поверхности (DRF-2198).
 *
 * Инцидент 20.09: `new Date(NaN).toISOString()` в карточке «Сегодня» бросил
 * RangeError, React снял корень — человек увидел белый экран без единой
 * кнопки и без выхода. Точечная причина закрыта (#1918); здесь — класс:
 * любое исключение рендера даёт понятное состояние с повтором.
 *
 * Одна граница на приложение, в `AppShell` вокруг возвращаемой поверхности
 * (отступление (ю)): диспетчер отдаёт ровно одно дерево маршрутов
 * (мастер / соло / админ / клиент), и четыре одинаковые обёртки были бы
 * четырьмя местами, где можно забыть.
 *
 * `chrome` — навигация, которая должна пережить исключение (панель, выход).
 * Её рисует вызывающий: граница не знает, какая поверхность упала.
 *
 * В лог уходит сам `Error` — без тела ответа и без ПДн: сюда попадают
 * исключения рендера, а не сетевые ответы.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

import { SystemState } from "./master/SystemState";

interface Props {
  children: ReactNode;
  /** Навигация, остающаяся под состоянием ошибки. */
  chrome?: ReactNode;
  onError?: (err: Error, info: ErrorInfo) => void;
}

interface State {
  err: Error | null;
  /** Меняется на «Попробовать снова» — дети монтируются заново. */
  attempt: number;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null, attempt: 0 };

  static getDerivedStateFromError(err: Error): Partial<State> {
    return { err };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    this.props.onError?.(err, info);
  }

  private retry = (): void => {
    this.setState((s) => ({ err: null, attempt: s.attempt + 1 }));
  };

  render(): ReactNode {
    const { children, chrome } = this.props;
    const { err, attempt } = this.state;
    if (err !== null) {
      return (
        <div className="screen">
          <SystemState kind="load_error" what="screen" err={err} onRetry={this.retry} />
          {chrome}
        </div>
      );
    }
    // key: повтор перемонтирует поддерево, а не просто убирает состояние.
    return <div key={attempt}>{children}</div>;
  }
}
