import { describe, expect, it } from 'vitest';
import { dataOceanTheme } from './themeConfig';
import { oceanTokens, statusTone } from './tokens';

describe('Data Ocean theme', () => {
  it('uses the approved Polar Mist palette', () => {
    expect(oceanTokens.canvas.start).toBe('#A9D2DF');
    expect(oceanTokens.canvas.end).toBe('#E8F3F5');
    expect(oceanTokens.text.primary).toBe('#102F44');
    expect(dataOceanTheme.token?.colorPrimary).toBe('#1686AE');
    expect(dataOceanTheme.token?.borderRadius).toBe(4);
  });

  it('maps every status to a text label tone and marker', () => {
    expect(statusTone.success).toMatchObject({ color: '#14765E', marker: 'solid' });
    expect(statusTone.danger).toMatchObject({ color: '#A53D52', marker: 'cross' });
  });
});
