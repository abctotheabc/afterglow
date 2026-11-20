import test from 'node:test';
import assert from 'node:assert/strict';
import {groupMovies} from '../public/catalog.js';
test('unknown year joins only an unambiguous same-title movie',()=>{assert.equal(groupMovies([{title:'Film',year:2026},{title:'Film',year:null}]).size,1);});
test('different remakes and ambiguous missing-year entries stay separate',()=>{assert.equal(groupMovies([{title:'Film',year:1980},{title:'Film',year:2026},{title:'Film',year:null}]).size,3);});
test('explicit matching year suffix joins same movie without losing showtimes',()=>{const groups=groupMovies([{title:'Film (2026)',year:2026},{title:'Film',year:2026}]);assert.equal(groups.size,1);assert.equal([...groups.values()][0].length,2);});
