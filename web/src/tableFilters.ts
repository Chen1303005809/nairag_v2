export interface TableFilterOption {
  text: string;
  value: string;
}

/**
 * Builds the filter menu from the data currently displayed by a table.
 * Values are kept stable for filtering while labels remain user-friendly.
 */
export function uniqueTableFilterOptions<T>(
  records: readonly T[],
  getOptions: (record: T) => readonly TableFilterOption[]
): TableFilterOption[] {
  const options = new Map<string, TableFilterOption>();

  for (const record of records) {
    for (const option of getOptions(record)) {
      if (!options.has(option.value)) {
        options.set(option.value, option);
      }
    }
  }

  return [...options.values()].sort((left, right) => left.text.localeCompare(right.text, "zh-CN"));
}
