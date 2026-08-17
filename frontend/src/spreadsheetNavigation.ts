export type SpreadsheetNavigator = (url: string) => void;

export const navigateToSpreadsheet: SpreadsheetNavigator = (url) => {
  window.location.assign(url);
};
