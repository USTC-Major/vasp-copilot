import { describe, expect, it } from 'vitest';
import { layoutDagTracks } from './dagLayout';

describe('file inheritance label rails', () => {
  const boxes = [{ id: 'a', x: 50, y: 50, width: 220, height: 160 }, { id: 'b', x: 310, y: 50, width: 220, height: 160 }];
  const dependencies = ['CONTCAR → POSCAR', 'CHGCAR → CHGCAR', 'very-long-source-file.xml → very-long-target-file.xml'].map((label, i) => ({ id: String(i), source: 'a', target: 'b', label }));
  it('keeps long labels outside every node and separate from one another', () => {
    for (const nodes of [boxes, [boxes[0], { ...boxes[1], x: 120, y: -90 }]]) {
      const tracks = layoutDagTracks(nodes, dependencies);
      expect(tracks).toHaveLength(3);
      for (const track of tracks) {
        expect(track.y + track.height).toBeLessThan(Math.min(...nodes.map(node => node.y)));
        for (const other of tracks.filter(item => item.id !== track.id)) {
          expect(track.y + track.height <= other.y || other.y + other.height <= track.y || track.x + track.width <= other.x || other.x + other.width <= track.x).toBe(true);
        }
      }
      expect(tracks[2].width).toBeGreaterThan(350);
    }
  });
  it('omits a missing endpoint instead of manufacturing a dependency', () => {
    expect(layoutDagTracks(boxes, [{ id: 'unknown', source: 'a', target: 'missing', label: 'POSCAR' }])).toEqual([]);
  });
});
