/** Only these fixed messages may cross from transport errors into the UI. */
export class NotebookAccessError extends Error {
  constructor() {
    super('Your sign-in was not accepted for this notebook. Sign out and sign in again. If this continues, the household administrator needs to check access configuration.');
    this.name = 'NotebookAccessError';
  }
}

export function notebookErrorMessage(error: unknown, fallback: string): string {
  return error instanceof NotebookAccessError ? new NotebookAccessError().message : fallback;
}
