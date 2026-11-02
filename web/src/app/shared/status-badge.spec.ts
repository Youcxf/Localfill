import { TestBed } from '@angular/core/testing';
import { StatusBadge } from './status-badge';

// renders the badge and gives back the text it shows
function textFor(status: 'done' | 'dry_run' | 'failed'): string {
  const fixture = TestBed.createComponent(StatusBadge);
  fixture.componentRef.setInput('status', status);
  fixture.detectChanges();
  return fixture.nativeElement.textContent.trim();
}

describe('status badge', () => {
  it('shows the status as a word', () => {
    expect(textFor('done')).toBe('done');
    expect(textFor('failed')).toBe('failed');
  });

  it('writes dry run with a space', () => {
    expect(textFor('dry_run')).toBe('dry run');
  });
});
