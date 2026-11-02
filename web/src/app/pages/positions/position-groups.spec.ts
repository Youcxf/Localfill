import { OpenPosition } from '../../api/types';
import { groupPositions, parseContract, rolloverNotice } from './position-groups';

function position(contract: string, qty: number, account = 'DEMO1001', connection = 'Prop 1'): OpenPosition {
  return {
    connection,
    account_id: Number(account.slice(-4)),
    account_name: account,
    contract_id: 1,
    contract_name: contract,
    net_pos: qty,
  };
}

describe('parse contract', () => {
  it('splits the root from the month and year', () => {
    expect(parseContract('MNQZ6')).toEqual({ root: 'MNQ', expiry: 6 * 12 + 11 });
    expect(parseContract('ESH7')?.root).toBe('ES');
  });

  it('gives null for a name that is not a contract', () => {
    expect(parseContract('MNQ')).toBeNull();
    expect(parseContract('nonsense')).toBeNull();
  });
});

describe('group positions', () => {
  it('groups by contract and adds up the net position', () => {
    const groups = groupPositions([
      position('MNQZ6', 4),
      position('MNQZ6', 4, 'DEMO1002'),
      position('ESZ6', -1),
    ]);
    expect(groups.map((g) => g.contract)).toEqual(['MNQZ6', 'ESZ6']);
    expect(groups[0].net).toBe(8);
    expect(groups[0].summary).toBe('net +8 across 2 accounts');
    expect(groups[1].summary).toBe('net −1 on 1 account');
  });

  it('marks each row long or short', () => {
    const rows = groupPositions([position('MNQZ6', 4), position('MNQZ6', -2, 'DEMO1002')])[0].rows;
    expect(rows.map((r) => [r.qty, r.side])).toEqual([
      ['+4', 'long'],
      ['−2', 'short'],
    ]);
  });

  it('counts an account once even with two positions on it', () => {
    const groups = groupPositions([position('MNQZ6', 1), position('MNQZ6', 2)]);
    expect(groups[0].accounts).toBe(1);
  });

  it('flags the earlier month when the same root is open in two months', () => {
    const groups = groupPositions([position('MNQU6', 2), position('MNQZ6', 2)]);
    expect(groups.find((g) => g.contract === 'MNQU6')?.old).toBe(true);
    expect(groups.find((g) => g.contract === 'MNQZ6')?.old).toBe(false);
  });

  it('knows that year 0 comes after year 9', () => {
    const groups = groupPositions([position('MNQZ9', 1), position('MNQH0', 1)]);
    expect(groups.find((g) => g.contract === 'MNQZ9')?.old).toBe(true);
    expect(groups.find((g) => g.contract === 'MNQH0')?.old).toBe(false);
  });

  it('does not compare different roots', () => {
    const groups = groupPositions([position('MNQU6', 1), position('ESZ6', 1)]);
    expect(groups.every((g) => !g.old)).toBe(true);
  });
});

describe('rollover notice', () => {
  it('is empty when every root has one month', () => {
    expect(rolloverNotice(groupPositions([position('MNQZ6', 1), position('ESZ6', 1)]))).toBe('');
  });

  it('names the contracts that are open together', () => {
    const groups = groupPositions([position('MNQU6', 1), position('MNQZ6', 1)]);
    expect(rolloverNotice(groups)).toBe(
      'MNQ is open in two contract months, MNQU6 and MNQZ6. Close the old month before it expires.',
    );
  });
});
