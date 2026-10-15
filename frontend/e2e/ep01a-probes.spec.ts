/**
 * EP-01A — dialog implementation probes (browser).
 *
 * Measures the SAME reviewed-action dialog rendered by four implementations
 * with identical content: initial focus, tab cycle, Shift+Tab wrap, Escape,
 * focus return to the opener, busy-state dismissal blocking and ARIA wiring.
 *
 * Assertions cover the shared contract; the recorded JSON carries the detailed
 * measurements the ADR compares.
 */
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { openPrototype, record, shot } from './ep01a-evidence'

const IMPLS = [
  { id: 'existing', label: 'OpenJM pattern (current)' },
  { id: 'radix', label: 'Radix Primitives' },
  { id: 'aria', label: 'React Aria Components' },
  { id: 'baseui', label: 'Base UI' },
]

async function activeDescriptor(page: Page): Promise<string> {
  return page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null
    if (!element) return '(none)'
    return (
      element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 80) || element.tagName.toLowerCase()
    )
  })
}

for (const impl of IMPLS) {
  test(`probe: ${impl.label}`, async ({ page }, testInfo: TestInfo) => {
    await openPrototype(page, 'probes')
    const opener = page.getByRole('button', { name: `Open ${impl.label} probe` })
    const dialog = page.getByRole('dialog')

    await opener.focus()
    await opener.click()
    await expect(dialog).toBeVisible()
    await page.waitForTimeout(80)

    const aria = await dialog.evaluate((element) => {
      const labelledby = element.getAttribute('aria-labelledby')
      const describedby = element.getAttribute('aria-describedby')
      return {
        role: element.getAttribute('role'),
        ariaModal: element.getAttribute('aria-modal'),
        labelledbyText: labelledby ? document.getElementById(labelledby)?.textContent ?? null : null,
        describedbyText: describedby ? document.getElementById(describedby)?.textContent ?? null : null,
        tag: element.tagName.toLowerCase(),
      }
    })
    const focusAfterOpen = await activeDescriptor(page)

    const focusOrder: string[] = [focusAfterOpen]
    for (let step = 0; step < 6; step += 1) {
      await page.keyboard.press('Tab')
      focusOrder.push(await activeDescriptor(page))
    }
    await page.keyboard.press('Shift+Tab')
    const afterShiftTab = await activeDescriptor(page)

    // Tab containment: after the first hop into the dialog, every stop is one
    // of the dialog's own controls, and reverse wrap also stays inside.
    const dialogControls = ['Cancel', 'Confirm probe action']
    expect(
      focusOrder.slice(1).every((entry) => dialogControls.includes(entry)),
      `tab cycle left the dialog: ${JSON.stringify(focusOrder)}`,
    ).toBe(true)
    expect(dialogControls).toContain(afterShiftTab)

    await page.keyboard.press('Escape')
    await expect(dialog).not.toBeVisible()
    const focusAfterClose = await activeDescriptor(page)
    const focusRestored = focusAfterClose === `Open ${impl.label} probe`

    // Busy-state contract: Escape and outside dismissal are ignored while saving.
    await opener.click()
    await expect(dialog).toBeVisible()
    await page.evaluate(() => {
      ;(window as unknown as { __ep01a: { setLatency: (ms: number) => void } }).__ep01a.setLatency(800)
    })
    await dialog.getByRole('button', { name: 'Confirm probe action' }).click()
    await page.waitForTimeout(120)
    const cancelDisabled = await dialog.getByRole('button', { name: 'Cancel' }).isDisabled()
    await page.keyboard.press('Escape')
    await page.waitForTimeout(150)
    const escapeIgnoredWhileBusy = await dialog.isVisible()
    await page.mouse.click(4, 4)
    await page.waitForTimeout(150)
    const outsideIgnoredWhileBusy = await dialog.isVisible()
    await shot(page, testInfo, `ep01a-probe-${impl.id}`, false)
    await expect(dialog).not.toBeVisible()

    record(`probe-${impl.id}.json`, {
      implementation: impl,
      aria,
      focusAfterOpen,
      focusOrder,
      afterShiftTab,
      focusAfterClose,
      focusRestored,
      busy: { cancelDisabled, escapeIgnoredWhileBusy, outsideIgnoredWhileBusy },
    })

    expect(aria.role).toBe('dialog')
    expect(aria.labelledbyText).toBe('Probe: reviewed action')
    expect(aria.describedbyText).toBe(
      'Standard probe content for the dialog comparison.',
    )
    expect(focusRestored, `focus should return to the opener (was: ${focusAfterClose})`).toBe(true)
    expect(cancelDisabled).toBe(true)
    expect(escapeIgnoredWhileBusy).toBe(true)
    expect(outsideIgnoredWhileBusy).toBe(true)
  })
}
