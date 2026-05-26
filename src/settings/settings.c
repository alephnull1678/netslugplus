/* main.c
 *   by Alex Chadwick
 * 
 * Copyright (C) 2013-2015, Alex Chadwick
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include "settings.h"

#include <fcntl.h>
#include <malloc.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>

#include "main.h"

typedef struct settings_fileParseState_t {
  const settings_category_t *category;
  settings_value_t *value;
  /** 0 = not, 1 = in comment, 2 = line aborted to comment, 3 = in category [, 4 = has space */
  int state;
  char buffer[256];
  unsigned int bufferLocation;
} settings_fileParseState_t;

/* Format for settings:
 * hidden, name, address, type [, min, max, comment].
 */
static settings_value_t network_settings[] = {
    { 0, "host_ip", &host_ip, settings_variableType_ip },
    { 0, "port", &port, settings_variableType_int, 1, 65535 },
    { 0, "relay_enabled", &relay_enabled, settings_variableType_bool },
    { 0, "relay_ip", &relay_ip, settings_variableType_ip },
    { 0, "relay_port", &relay_port, settings_variableType_int, 1, 65535 },
};

#define SETTINGS_CATEGORY(x) { #x, x ## _settings, sizeof(x ## _settings) / sizeof(settings_value_t) }

const settings_category_t settings_categories[] = {
    SETTINGS_CATEGORY(network),
};
const unsigned int settings_categoriesCount = sizeof(settings_categories)
    / sizeof(settings_category_t);

static const char settings_path[] = APP_PATH "/config.ini";

static void settings_parseUpdate(settings_fileParseState_t *state, char *text,
    unsigned int length);
static void settings_parseCategory(settings_fileParseState_t *state);
static void settings_parseName(settings_fileParseState_t *state);
static void settings_commit(settings_fileParseState_t *state);

void settings_init(void) {
  const settings_category_t *c;
  settings_value_t *s;
  for (c = settings_categories;
      c < settings_categories + settings_categoriesCount; c++)
    for (s = c->settings; s < c->settings + c->count; s++) {
      s->defaultValue._int = *(int*)s->address;
    }
}
void settings_load(void) {
  int fd, len;
  static char buffer[256];
  settings_fileParseState_t state;

  state.category = 0;
  state.bufferLocation = 0;
  state.value = 0;
  state.state = 0;

  fd = open(settings_path, O_RDONLY, 0);
  if (fd == -1) {
    settings_save();
    return;
  }

  do {
    len = read(fd, buffer, sizeof(buffer));

    settings_parseUpdate(&state, buffer, len);
  } while (len > 0);
  if (!(state.state & 3)) {
    settings_commit(&state);
  }

  close(fd);
}
void settings_save(void) {
  int fd, len;
  static char buffer[32 + 3 + 256 + 2 + 1];
  const settings_category_t *c;
  settings_value_t *s;
  int category;

  fd = open(settings_path, O_WRONLY | O_CREAT | O_TRUNC, 0);
  if (fd == -1) return;

  len = sprintf(buffer, "# Auto generated config for netslug\r\n");
  write(fd, buffer, len);

  for (c = settings_categories, category = 0;
      c < settings_categories + settings_categoriesCount; c++, category = 0)
    for (s = c->settings; s < c->settings + c->count; s++) {
      if (!s->hidden || *(int *)s->address != s->defaultValue._int) {
        if (!category) {
          len = sprintf(buffer, "\r\n[%s]\r\n", c->name);
          category = 1;
          write(fd, buffer, len);
        }
        if (s->comment) {
          len = sprintf(buffer, "# %s\r\n", s->comment);
          write(fd, buffer, len);
        }
        switch (s->type) {
          case settings_variableType_bool:
            len = sprintf(buffer, "%s = %s\r\n", s->name,
                *(int *)s->address ? "yes" : "no");
            break;
          case settings_variableType_int:
            len = sprintf(buffer, "%s = %d\r\n", s->name, *(int *)s->address);
            break;
          case settings_variableType_charPtr:
            len = sprintf(buffer, "%s = %s\r\n", s->name, *(char **)s->address);
            break;
          case settings_variableType_ip:
            len = sprintf(
              buffer, "%s = %d.%d.%d.%d\r\n", s->name,
              (int)((unsigned char *)s->address)[0], (int)((unsigned char *)s->address)[1],
              (int)((unsigned char *)s->address)[2], (int)((unsigned char *)s->address)[3]);
            break;
        }
        write(fd, buffer, len);
      }
    }

  close(fd);
}

static void settings_parseUpdate(settings_fileParseState_t *state, char *text,
    unsigned int length) {
  while (length--) {
    switch (*text) {
      case 0:
        break;
      case '#':
        if (!(state->state & 3)) {
          // A # ends the line if in x = y
          settings_commit(state);
          state->state = 1;
        } else if (state->state == 3) {
          // A # is invalid within [ ]
          state->state = 2;
        }
        break;
      case '\n':
      case '\r':
        if (!(state->state & 3)) {
          // A new line ends the line if in x = y
          settings_commit(state);
        }
        // A new line always resets to x = y
        state->value = NULL;
        state->state = 0;
        state->bufferLocation = 0;
        break;
      case '[':
        if (state->bufferLocation || state->value || (state->state & 3)) {
          // A [ is an error if text has occurred before it
          state->state = 2;
        } else if (!(state->state & 3)) {
          // A [ starts a  [ ]
          state->state = 3;
        }
        break;
      case ']':
        if (state->state == 3) {
          // A ] ends a [ ]
          settings_parseCategory(state);
        } else if (!(state->state & 3)) {
          // Otherwise it is wrong and invalid.
          state->state = 2;
        }
        break;
      case '=':
        if (!(state->state & 3)) {
          // = switches to the value part of a x = y statement
          if (!state->category) {
            state->state = 2;
          } else {
            settings_parseName(state);
          }
        } else if (state->state == 3) {
          // = is invalid in [ ]
          state->state = 2;
        }
        break;
      case ' ':
      case '\t':
        if (!(state->state & 3) && state->bufferLocation != 0)
          // Whitespace is reduced in x = y
          state->state |= 4;
        else if (state->state == 3)
        // It is invalid in [ ]
          state->state = 2;
        break;
      default:
        if ((state->state & 3) == 0 || state->state == 3) {
          // characters are valid in [ ] or x = y
          if (state->state & 4) {
            // insert a single space if any were encountered
            if (state->bufferLocation == sizeof(state->buffer) - 1) {
              // more than 255 characters are invalid.
              state->state = 2;
              break;
            }
            state->buffer[state->bufferLocation++] = ' ';
          }
          if (state->bufferLocation == sizeof(state->buffer) - 1) {
            // more than 255 characters are invalid.
            state->state = 2;
            break;
          }
          state->buffer[state->bufferLocation++] = *text;
        }
        break;
    }
    text++;
  }
}
static void settings_parseCategory(settings_fileParseState_t *state) {
  const settings_category_t *c;
  state->category = NULL;
  state->buffer[state->bufferLocation] = '\0';
  for (c = settings_categories;
      c < settings_categories + settings_categoriesCount; c++) {
    if (!strcasecmp(c->name, state->buffer)) {
      state->category = c;
      break;
    }
  }

  state->state = 0;
  state->bufferLocation = 0;
}
static void settings_parseName(settings_fileParseState_t *state) {
  settings_value_t *s;
  state->value = NULL;
  state->buffer[state->bufferLocation] = '\0';
  for (s = state->category->settings;
      s < state->category->settings + state->category->count; s++) {
    if (!strcasecmp(s->name, state->buffer)) {
      state->value = s;
      break;
    }
  }

  if (!state->value) {
    state->state = 2;
  } else state->state = 0;
  state->bufferLocation = 0;
}
static void settings_commit(settings_fileParseState_t *state) {
  int location;
  int sign, digit, value = 0, octet, c;
  int radix = 10;
  if (state->value && !(state->state & 3)) {
    state->buffer[state->bufferLocation] = '\0';
    switch (state->value->type) {
      case settings_variableType_int:
        location = 0;

        if (state->buffer[0] == '+') {
          sign = 1;
          location++;
        } else if (state->buffer[0] == '-') {
          sign = -1;
          location++;
        } else {
          sign = 1;
        }

        if (state->buffer[location] == '0'
            && (state->buffer[location + 1] == 'x'
                || state->buffer[location + 1] == 'X')) {
          radix = 16;
          location += 2;
        } else if (state->buffer[location] == 'b'
            || state->buffer[location] == 'B') {
          radix = 2;
          location++;
        } else if (state->buffer[location] == '0') {
          radix = 8;
          location++;
        }

        if (!state->buffer[location]) {
          return;
        }

        while (state->buffer[location]) {
          if (state->buffer[location] >= '0' && state->buffer[location] <= '9')
            digit = state->buffer[location++] - '0';
          else if (state->buffer[location] >= 'a'
              && state->buffer[location] <= 'z')
            digit = state->buffer[location++] - 'a' + 10;
          else if (state->buffer[location] >= 'A'
              && state->buffer[location] <= 'Z')
            digit = state->buffer[location++] - 'A' + 10;
          else {
            return;
          }

          if (digit >= radix) {
            return;
          }
          value *= radix;
          value += digit;
        }
        value *= sign;

        if (value < state->value->min || value > state->value->max) {
          state->state = 2;
          return;
        }
        *(int*)state->value->address = value;
        break;
      case settings_variableType_bool:
        if (state->buffer[0] == '0' || state->buffer[0] == 'f'
            || state->buffer[0] == 'F' || state->buffer[0] == 'n'
            || state->buffer[0] == 'N')
          *(int*)state->value->address = 0;
        else if (state->buffer[0] == '1' || state->buffer[0] == 't'
            || state->buffer[0] == 'T' || state->buffer[0] == 'y'
            || state->buffer[0] == 'Y') *(int*)state->value->address = 1;
        break;
      case settings_variableType_charPtr:
        // FIXME memory leak
        *(char **)state->value->address = memalign(32,
            state->bufferLocation + 1);
        memcpy(*(char **)state->value->address, state->buffer,
            state->bufferLocation + 1);
        break;
      case settings_variableType_ip:
        value = 0;
        location = 0;
        for (octet = 0; octet < 4; octet++) {
          for (c = 0; c < 3; c++) {
            if (state->buffer[location] >= '0' && state->buffer[location] <= '9')
              value = (value & 0xffffff00) | (((value & 0xff) * 10 + (state->buffer[location] - '0')) & 0xff);
            else
              return;
            location++;
            if (state->buffer[location] == '.' || state->buffer[location] == '\0') break;
          }
		  if (octet == 3 && state->buffer[location] == '\0') break;
          if (state->buffer[location] != '.') return;
          location++;
          value <<= 8;
        }
        *(int*)state->value->address = value;
        break;
    }
  }
}
