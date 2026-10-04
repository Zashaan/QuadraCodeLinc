export type Logger = (
  event: string,
  fields?: Record<string, string | number | boolean>,
) => void;

export const log: Logger = (event, fields = {}) => {
  process.stdout.write(
    `${JSON.stringify({ time: new Date().toISOString(), event, ...fields })}\n`,
  );
};
